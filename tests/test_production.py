import json
from pathlib import Path
import subprocess
import tarfile
import unittest
from unittest.mock import patch

import test_maintenance as base
from test_maintenance import compose, m


class ProductionTests(unittest.TestCase):
    setUp = base.MaintenanceTests.setUp
    def test_new_config_validation(self):
        for key, value in [('STOP_TIMEOUT', '0'), ('BACKUP_TIMEOUT', '-1'),
                           ('RESTORE_TIMEOUT', 'no'), ('IMAGE_ARCHIVE_TIMEOUT', '0'),
                           ('SPACE_MARGIN_PERCENT', '0'), ('BACKUP_COMPRESSION', 'zip'),
                           ('BACKUP_COPY_MOUNT', 'relative')]:
            with self.subTest(key=key):
                original = self.config.read_text()
                self.config.write_text(original + f'{key}="{value}"\n')
                with self.assertRaises(m.Failure):
                    m.read_config(self.config)
                self.config.write_text(original)

    def test_same_filesystem_space_is_summed_with_reserve(self):
        with patch.object(m.shutil, 'disk_usage', return_value=m.shutil._ntuple_diskusage(100, 20, 80)), \
             patch.object(self.manager, 'log'):
            self.manager.cfg['MIN_FREE_MB'] = '0'
            self.manager.cfg['SPACE_MARGIN_PERCENT'] = '20'
            with self.assertRaises(m.Failure):
                self.manager.space_check([('archive', self.root, 40), ('copy', self.root, 40)])
            self.manager.space_check([('archive', self.root, 20), ('copy', self.root, 20)])

    def test_remote_missing_mount_rejected_without_local_write(self):
        target = self.root / 'remote'
        target.mkdir()
        self.manager.cfg.update(BACKUP_COPY_DIR=str(target), BACKUP_COPY_MOUNT=str(target),
                                BACKUP_COPY_SOURCE='server:/backup')
        with patch.object(self.manager, 'mount_info', return_value={'target': '/', 'source': '/dev/root'}):
            with self.assertRaises(m.Failure):
                self.manager.remote_target()
        self.assertEqual(list(target.iterdir()), [])

    def test_remote_wrong_source_rejected(self):
        self.manager.cfg.update(BACKUP_COPY_DIR=str(self.root), BACKUP_COPY_MOUNT=str(self.root),
                                BACKUP_COPY_SOURCE='server:/backup')
        with patch.object(self.manager, 'mount_info', return_value={'target': str(self.root), 'source': '/dev/root'}):
            with self.assertRaises(m.Failure):
                self.manager.remote_target()

    def test_remote_source_requires_expected_mount(self):
        with self.config.open('a') as handle:
            handle.write('BACKUP_COPY_SOURCE="server:/backup"\n')
        with self.assertRaises(m.Failure):
            m.read_config(self.config)

    def test_data_disk_checks_actual_volume_mounts(self):
        self.manager.cfg['DATA_MOUNT'] = str(self.root)
        inventory = {'root': self.root, 'volumes': {'psql_data_vol': self.root}, 'volume_bytes': {}, 'image_bytes': 0}
        with patch.object(self.manager, 'storage_inventory', return_value=inventory), \
             patch.object(self.manager, 'mount_info', return_value={'target': '/', 'source': '/dev/root'}), \
             patch.object(self.manager, 'log'):
            with self.assertRaises(m.Failure):
                self.manager.report_storage(compose())

    def test_stop_never_requests_daemon_kill_deadline(self):
        state = [{'Id': 'db', 'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                  'State': {'Status': 'exited', 'ExitCode': 0}}]
        def docker(*args, **kwargs):
            if args[:2] == ('ps', '-aq'):
                return 'db'
            if args[0] == 'inspect':
                return json.dumps(state)
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker) as docker, \
             patch.object(self.manager, 'states', return_value={}):
            self.manager.stop(compose())
        stop = [call for call in docker.call_args_list if call.args[0] == 'stop']
        self.assertEqual(stop[0].args, ('stop', '--timeout', '-1', 'db'))
        self.assertEqual(stop[0].kwargs['timeout'], int(self.manager.cfg['STOP_TIMEOUT']))

    def test_stop_timeout_reinspects_and_does_not_archive_or_remove_db(self):
        inspected = []
        self.manager.state.mkdir()
        self.manager.write_pending('backup')
        def docker(*args, **kwargs):
            if args[:2] == ('ps', '-aq'):
                return 'db'
            if args[0] == 'stop':
                raise m.Failure('client timed out')
            if args[0] == 'inspect':
                inspected.append(True)
                return json.dumps([{'Id': 'db', 'State': {'Status': 'running', 'ExitCode': 0}}])
            self.assertNotIn(args[0], ('rm', 'kill'))
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker), patch.object(self.manager, 'log'):
            with self.assertRaises(m.Failure):
                self.manager.stop(compose())
        self.assertTrue(inspected)
        self.assertTrue(self.manager.pending.exists())

    def test_postgres_abnormal_exit_blocks_snapshot(self):
        def docker(*args, **kwargs):
            if args[:2] == ('ps', '-aq'):
                return 'db'
            if args[0] == 'inspect':
                return json.dumps([{'Id': 'db', 'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                                    'State': {'Status': 'exited', 'ExitCode': 1}}])
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker):
            with self.assertRaises(m.Failure):
                self.manager.stop(compose())

    def test_budget_caps_command_timeout(self):
        self.manager.cfg['BACKUP_TIMEOUT'] = '5'
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            with self.manager.budget('BACKUP_TIMEOUT'):
                self.manager.run(['true'], timeout=100)
        self.assertLessEqual(run.call_args.kwargs['timeout'], 5)

    def test_tar_import_selects_gzip_and_restore_timeout(self):
        path = self.root / 'v.tar.gz'
        with tarfile.open(path, 'w:gz'):
            pass
        with patch.object(self.manager, 'docker') as docker:
            self.manager.volume_import('image', 'volume', path)
        self.assertIn('-xzpf', docker.call_args.args)
        self.assertEqual(docker.call_args.kwargs['timeout'], int(self.manager.cfg['RESTORE_TIMEOUT']))

    def test_scanner_reports_only_concurrency_and_does_not_modify_tasks(self):
        with patch.object(self.manager, 'dc', side_effect=['OpenVAS 23.example\n',
                         'max_hosts = 5\nsecret = private\nmax_checks = 2\n']) as dc, \
             patch.object(self.manager, 'log') as log:
            self.manager.report_scanner(compose())
        self.assertEqual(dc.call_count, 2)
        self.assertTrue(all(call.args[1] == 'exec' for call in dc.call_args_list))
        self.assertNotIn('private', str(log.call_args_list))
        self.assertIn('max_checks', str(log.call_args_list))

    def test_restore_inventory_needs_no_preinstalled_images(self):
        def docker(*args, **kwargs):
            if args[0] == 'info':
                return str(self.root)
            self.assertEqual(args, ('volume', 'ls', '-q'))
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker):
            inventory = self.manager.storage_inventory(compose(), images=False)
        self.assertEqual(inventory['image_bytes'], 0)

    def test_file_hash_checks_budget_between_blocks(self):
        path = self.root / 'large'
        path.write_bytes(b'x' * 2 * 1024 * 1024)
        with self.assertRaises(m.Failure):
            m.file_hash(path, lambda: (_ for _ in ()).throw(m.Failure('expired')))

    def test_restore_can_replace_previously_crashed_database_but_backup_cannot(self):
        state = {'Id': 'db', 'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                 'State': {'Status': 'exited', 'ExitCode': 137, 'OOMKilled': True}}
        def docker(*args, **kwargs):
            if args[:2] == ('ps', '-aq'):
                return 'db'
            if args[0] == 'inspect':
                return json.dumps([state])
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker):
            with self.assertRaises(m.Failure):
                self.manager.stop(compose())
            self.manager.stop(compose(), recovery=True)

    def test_restore_rejects_new_abnormal_shutdown_of_running_database(self):
        before = {'Id': 'db', 'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                  'State': {'Status': 'running', 'ExitCode': 0}}
        after = {**before, 'State': {'Status': 'exited', 'ExitCode': 137}}
        inspections = iter([before, after])
        def docker(*args, **kwargs):
            if args[:2] == ('ps', '-aq'):
                return 'db'
            if args[0] == 'inspect':
                return json.dumps([next(inspections)])
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker):
            with self.assertRaises(m.Failure):
                self.manager.stop(compose(), recovery=True)

    def test_probe_pins_target_before_detach_and_never_writes_fallback(self):
        remote = self.root / 'remote'
        remote.mkdir()
        detached = self.root / 'detached'
        self.manager.cfg.update(BACKUP_COPY_DIR=str(remote), BACKUP_COPY_MOUNT=str(remote),
                                BACKUP_COPY_SOURCE='server:/backup')
        calls = 0
        def mount_info(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                remote.rename(detached)
                remote.mkdir()
            return {'target': str(remote), 'source': 'server:/backup'}
        with patch.object(self.manager, 'mount_info', side_effect=mount_info), \
             patch.object(m.tempfile, 'TemporaryFile') as probe:
            with self.assertRaises(m.Failure):
                self.manager.remote_target()
        probe.assert_not_called()
        self.assertEqual(list(remote.iterdir()), [])

    def test_stop_preserves_image_and_mount_drift_gate(self):
        state = {'Id': 'db', 'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                 'State': {'Status': 'exited', 'ExitCode': 0}}
        def docker(*args, **kwargs):
            if args[:2] == ('ps', '-aq'):
                return 'db'
            if args[0] == 'inspect':
                return json.dumps([state])
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker), \
             patch.object(self.manager, 'states', side_effect=m.Failure('mount/image drift')) as drift:
            with self.assertRaises(m.Failure):
                self.manager.stop(compose())
        drift.assert_called_once()

    def test_image_archive_failure_checks_daemon_without_killing_stack(self):
        self.manager.state.mkdir()
        self.manager.write_pending('backup')
        with patch.object(self.manager, 'run', side_effect=[m.Failure('archive timed out'), 'daemon ready']) as run, \
             patch.object(self.manager, 'log') as log:
            with self.assertRaises(m.Failure):
                self.manager.docker('image', 'save', '-o', '/unused/archive', 'saved-tag')
        self.assertEqual(run.call_args_list[1].args[0], ['docker', 'info'])
        self.assertIn('completion is unknown', str(log.call_args_list))
        self.assertTrue(self.manager.pending.exists())
        self.assertNotIn('kill', str(run.call_args_list))
