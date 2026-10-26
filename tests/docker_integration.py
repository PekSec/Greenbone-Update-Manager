#!/usr/bin/env python3
"""Opt-in real Docker cold PostgreSQL backup/restore; never deploys Greenbone.

Uses cached postgres:17-alpine as a stand-in for the image archive/tar helper.
Only the PostgreSQL service starts. GMP and Greenbone compatibility are stubbed;
all archive, checksum, image load, volume restoration and SQL checks are real.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import maintenance as m


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-mb', type=int, default=64, help='Synthetic uncompressed database payload (1..4096 MiB)')
    parser.add_argument('--compression', choices=('none', 'gzip'), default='none')
    args = parser.parse_args()
    if not 1 <= args.data_mb <= 4096:
        parser.error('--data-mb must be 1..4096')
    os.umask(0o077)
    project = 'gb-test-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='gb-integration-') as directory:
        root = Path(directory)
        config = root / 'update.env'
        config.write_text(f'DOWNLOAD_DIR="{root}/state"\nPROJECT="{project}"\nMIN_FREE_MB="1"\n')
        config.chmod(0o600)
        manager = m.Manager(m.read_config(config))
        manager.cfg['BACKUP_COMPRESSION'] = args.compression
        manager.state.mkdir()
        credentials = Path(manager.cfg['GMP_CREDENTIALS_FILE'])
        credentials.write_text('{"username":"integration","password":"unused"}')
        credentials.chmod(0o600)
        image = manager.docker('image', 'inspect', '--format', '{{.Id}}', 'postgres:17-alpine').strip()
        c = {'services': {}, 'volumes': {v: {'name': project + '_' + v} for v in m.VOLUMES}}
        for service in m.SERVICES:
            c['services'][service] = {'image': image, 'volumes': [], 'environment': {}}
        for service in ('vulnerability-tests', 'data-objects', 'report-formats'):
            c['services'][service]['environment']['FEED_RELEASE'] = '24.10'
        c['services']['pg-gvm'].update(network_mode='none', stop_signal='SIGTERM', environment={'POSTGRES_HOST_AUTH_METHOD': 'trust'},
            volumes=[{'type': 'volume', 'source': 'psql_data_vol', 'target': '/var/lib/postgresql/data'}])
        c['services']['gvmd']['volumes'] = [{'type': 'volume', 'source': 'gvmd_data_vol', 'target': '/data'}]
        bundle = m.seal({'schema': 1, 'id': 'integration', 'compose': c, 'restored_from': 'test-stand-in',
            'references': {s: m.REGISTRY + m.IMAGE_REPOS.get(s, s) + ':stable' for s in m.SERVICES},
            'feed_release': '24.10', 'gvmd_major': 26, 'evidence': 'isolated plumbing test only'})
        m.validate_bundle(bundle)
        m.atomic(manager.current, m.canonical(bundle))

        def sql(compose, query):
            return manager.dc(compose, 'exec', '-T', '-u', 'postgres', 'pg-gvm', 'psql', '-XAt',
                              '-v', 'ON_ERROR_STOP=1', '-c', query, timeout=30).strip()

        def start_postgres(compose):
            manager.dc(compose, 'up', '-d', '--no-deps', '--pull', 'never', 'pg-gvm')
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    manager.dc(compose, 'exec', '-T', 'pg-gvm', 'sh', '-c',
                               'test "$(cat /proc/1/comm)" = postgres', timeout=10)
                    if sql(compose, 'SELECT 1') == '1':
                        return
                except m.Failure:
                    time.sleep(1)
            raise AssertionError('PostgreSQL did not become ready')

        try:
            with manager.lock():
                for volume in c['volumes'].values():
                    manager.docker('volume', 'create', volume['name'])
                start_postgres(c)
                sql(c, "CREATE TABLE maintenance_test(value text); INSERT INTO maintenance_test VALUES ('before-migration')")
                sql(c, 'CREATE TABLE payload(value text); ALTER TABLE payload ALTER COLUMN value SET STORAGE EXTERNAL; '
                    "INSERT INTO payload SELECT repeat(md5(i::text),1024) FROM generate_series(1," + str(args.data_mb * 32) + ') AS i')
                expected_rows = str(args.data_mb * 32)
                assert sql(c, 'SELECT count(*) FROM payload') == expected_rows
                print('Synthetic PostgreSQL payload MiB=' + str(args.data_mb), flush=True)
                inventory = manager.capacity(c, 'backup')
                assert inventory['volume_bytes']['psql_data_vol'] >= args.data_mb * 1024 * 1024
                manager.docker('run', '--rm', '--pull=never', '--network', 'none', '--entrypoint', 'sh',
                    '--mount', 'type=volume,src=' + c['volumes']['gvmd_data_vol']['name'] + ',dst=/data',
                    image, '-c', 'printf essential-settings > /data/settings; chmod 600 /data/settings')
                # Smart PG shutdown must wait for a real client, even after our
                # bounded Docker CLI times out. Never force-kill the database.
                query_errors = []
                def long_client():
                    try:
                        sql(c, 'SELECT pg_sleep(6) /* graceful_shutdown_test */')
                    except Exception as error:
                        query_errors.append(error)
                thread = threading.Thread(target=long_client)
                thread.start()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if sql(c, "SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep(6)%' AND state='active'") == '1':
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError('Long-running client did not start')
                manager.write_pending('backup')
                manager.cfg['STOP_TIMEOUT'] = '1'
                try:
                    manager.stop(c)
                except m.Failure:
                    assert manager.pending.exists()
                    ids = manager.dc(c, 'ps', '-aq').split()
                    observed = json.loads(manager.docker('inspect', *ids))
                    assert observed[0]['State']['Running']
                else:
                    raise AssertionError('Slow PostgreSQL shutdown unexpectedly met one-second budget')
                thread.join(timeout=15)
                assert not thread.is_alive() and not query_errors
                manager.cfg['STOP_TIMEOUT'] = '60'
                manager.stop(c)
                ids = manager.dc(c, 'ps', '-aq').split()
                states = json.loads(manager.docker('inspect', *ids))
                assert all(x['State']['Status'] == 'exited' and x['State']['ExitCode'] == 0 for x in states)
                snapshot = manager.snapshot(bundle)
                manager.verify_backup(snapshot)
                assert all(p.stat().st_mode & 0o077 == 0 for p in snapshot.rglob('*'))
                # Simulate changed database content after an attempted upgrade.
                start_postgres(c)
                sql(c, "UPDATE maintenance_test SET value='after-migration'")
                manager.stop(c)
                manager.write_pending('upgrade', 'failed-release', snapshot)
                # A timed-out disposable import helper must be inspected and
                # removed, while old database volumes/content remain intact.
                failed_volume = project + '_failed_import'
                manager.docker('volume', 'create', failed_volume)
                original_run = manager.run
                failed_helpers = []
                def delayed_import(command, **kwargs):
                    if 'run' in command and any('dst=/dest' in part for part in command):
                        command = list(command)
                        failed_helpers.append(command[command.index('--name') + 1])
                        command[command.index('--entrypoint') + 1] = 'sh'
                        image_index = command.index(image)
                        command[image_index + 1:] = ['-c', 'sleep 5']
                        kwargs['timeout'] = 1
                    return original_run(command, **kwargs)
                with patch.object(manager, 'run', side_effect=delayed_import):
                    try:
                        manager.volume_import(image, failed_volume, manager.volume_archive(snapshot, 'psql_data_vol'))
                    except m.Failure as error:
                        assert error.code == 74 and manager.pending.exists()
                    else:
                        raise AssertionError('Delayed import unexpectedly succeeded')
                assert failed_helpers
                for helper in failed_helpers:
                    assert not manager.docker('ps', '-aq', '--filter', 'name=^/' + helper + '$').strip()
                with patch.object(manager, 'check_compatibility'), patch.object(manager, 'start', side_effect=start_postgres):
                    manager.restore(snapshot, project, True)
                restored = manager.load()
                assert sql(restored['compose'], 'SELECT value FROM maintenance_test') == 'before-migration'
                assert sql(restored['compose'], 'SELECT count(*) FROM payload') == expected_rows
                new_volume = restored['compose']['volumes']['gvmd_data_vol']['name']
                content = manager.docker('run', '--rm', '--pull=never', '--network', 'none', '--entrypoint', 'cat',
                    '--mount', 'type=volume,src=' + new_volume + ',dst=/data,readonly', image, '/data/settings')
                assert content == 'essential-settings'
                assert restored['compose']['volumes']['psql_data_vol']['name'] != c['volumes']['psql_data_vol']['name']
                assert all(m.DIGEST.fullmatch(s['image']) for s in restored['compose']['services'].values())
                assert not manager.pending.exists()
                print('PASS: PostgreSQL payload, slow graceful stop without SIGKILL, timed-out import cleanup, '
                      'image save/load, ' + args.compression + ' archives, fresh-volume restore and SQL/settings integrity')
        finally:
            # Remove only this test's project/container/volume/tag names.
            ids = manager.docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project).split()
            if ids:
                manager.docker('rm', '-f', *ids)
            volumes = manager.docker('volume', 'ls', '-q').split()
            own = [v for v in volumes if v.startswith(project + '_')]
            if own:
                manager.docker('volume', 'rm', *own)
            tags = manager.docker('image', 'ls', '--format', '{{.Repository}}:{{.Tag}}').split()
            own = [t for t in tags if t.startswith('greenbone-maintenance/' + project + ':')]
            if own:
                manager.docker('image', 'rm', *own)


if __name__ == '__main__':
    main()
