import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import shutil
from unittest.mock import patch, Mock
import unittest
import test_maintenance as base

from test_maintenance import compose, m


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        base.MaintenanceTests.setUp(self)
        self.bundle = m.seal({'schema': 1, 'id': 'baseline', 'compose': compose(),
            'references': {s: m.REGISTRY + m.IMAGE_REPOS.get(s, s) + ':stable' for s in m.SERVICES},
            'gvmd_major': 26, 'feed_release': '24.10', 'evidence': 'https://greenbone.github.io/docs/'})
        m.atomic(self.manager.current, m.canonical(self.bundle))
        target = copy.deepcopy(self.bundle)
        target['id'] = 'target'
        m.atomic(self.manager.release_path('target'), m.canonical(m.seal(target)))

    def backup_fixture(self):
        path = self.root / 'valid-backup'
        path.mkdir(mode=0o700)
        (path / 'volumes').mkdir(mode=0o700)
        for v in m.VOLUMES:
            if v not in m.SOCKETS:
                with tarfile.open(path / 'volumes' / (v + '.tar'), 'w'):
                    pass
        (path / 'release.json').write_bytes(m.canonical(self.bundle))
        (path / 'config.env').write_bytes(self.config.read_bytes())
        (path / 'credentials.json').write_text('{"username":"test","password":"private"}')
        images = {v['image'] for v in self.bundle['compose']['services'].values()}
        tags = {image: 'greenbone-maintenance/test:' + image.split('@sha256:')[1] for image in images}
        manifest = m.canonical([{'RepoTags': list(set(tags.values())), 'Config': 'config.json', 'Layers': []}])
        with tarfile.open(path / 'images.tar', 'w') as archive:
            for name, content in [('manifest.json', manifest), ('config.json', b'{}')]:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        metadata = {'schema': 1, 'kind': 'cold-all-volumes', 'project': 'test',
                    'volumes': {v: 'test_' + v for v in m.VOLUMES if v not in m.SOCKETS},
                    'image_tags': tags, 'image_fingerprints': {tag: 'f' * 64 for tag in tags.values()}}
        (path / 'backup.json').write_bytes(m.canonical(metadata))
        self.checksums(path)
        return path

    def checksums(self, path):
        files = [p for p in path.rglob('*') if p.is_file() and p.name != 'checksums.json']
        for f in files:
            f.chmod(0o600)
        (path / 'checksums.json').write_bytes(m.canonical({str(p.relative_to(path)): m.file_hash(p) for p in files}))

    def test_actual_upgrade_pull_failure_leaves_baseline_untouched(self):
        with patch.object(self.manager, 'check_compatibility'), patch.object(self.manager, 'scan_gate'), \
             patch.object(self.manager, 'health_once'), patch.object(self.manager, 'pull', side_effect=m.Failure('pull failed')), \
             patch.object(self.manager, 'stop') as stop:
            with self.assertRaises(m.Failure):
                self.manager.upgrade('target', True)
        stop.assert_not_called()
        self.assertFalse(self.manager.pending.exists())
        self.assertEqual(self.manager.load()['id'], 'baseline')

    def test_real_gate_blocks_active_scan_and_query_failure_before_stop(self):
        for reply in ('{"idle":false}', m.Failure('query failed')):
            with patch.object(self.manager, 'probe', side_effect=reply if isinstance(reply, Exception) else None,
                              return_value=reply), patch.object(self.manager, 'stop') as stop:
                with self.assertRaises(m.Failure) as ctx:
                    self.manager.backup(True)
            self.assertEqual(ctx.exception.code, 76)
            stop.assert_not_called()
            self.assertFalse(self.manager.pending.exists())

    def test_partial_migration_never_starts_old_set_or_restores_data(self):
        with patch.object(self.manager, 'check_compatibility'), patch.object(self.manager, 'scan_gate'), \
             patch.object(self.manager, 'health_once'), patch.object(self.manager, 'pull'), \
             patch.object(self.manager, 'protect_images'), patch.object(self.manager, 'stop'), \
             patch.object(self.manager, 'snapshot', return_value=self.root / 'backup'), \
             patch.object(self.manager, 'start', side_effect=m.Failure('migration failed')) as start, \
             patch.object(self.manager, 'restore') as restore:
            with self.assertRaises(m.Failure):
                self.manager.upgrade('target', True)
        self.assertEqual(start.call_count, 1)
        restore.assert_not_called()
        self.assertEqual(self.manager.load()['id'], 'baseline')
        pending = json.loads(self.manager.pending.read_bytes())
        self.assertEqual(pending['phase'], 'deploy')
        self.assertEqual(pending['backup'], str(self.root / 'backup'))

    def test_feed_update_pulls_only_seven_data_services(self):
        calls = []
        def pull(c, names):
            calls.append(tuple(names))
            for name in names:
                self.assertEqual(c['services'][name]['image'], m.REGISTRY + name + ':latest')
        def pin(c, names):
            for name in names:
                c['services'][name]['image'] = m.REGISTRY + name + '@sha256:' + 'b' * 64
        with patch.object(self.manager, 'check_compatibility'), patch.object(self.manager, 'scan_gate'), \
             patch.object(self.manager, 'pull', side_effect=pull), \
             patch.object(self.manager, 'pin', side_effect=pin), patch.object(self.manager, 'stop'), \
             patch.object(self.manager, 'start'), patch.object(self.manager, 'protect_images'):
            self.manager.feed_update(True)
        self.assertEqual(calls, [m.FEEDS])
        for name in set(m.SERVICES) - set(m.FEEDS):
            self.assertEqual(self.manager.load()['compose']['services'][name], self.bundle['compose']['services'][name])

    def test_valid_backup_then_corrupted_archive_rejected(self):
        path = self.backup_fixture()
        self.manager.verify_backup(path)
        with (path / 'volumes/psql_data_vol.tar').open('ab') as f:
            f.write(b'corruption')
        with self.assertRaises(m.Failure) as ctx:
            self.manager.verify_backup(path)
        self.assertEqual(ctx.exception.code, 74)

    def test_restore_failure_keeps_current_and_marker(self):
        path = self.backup_fixture()
        def docker(*args, **kw):
            if args[:2] == ('image', 'inspect'):
                return 'sha256:' + 'c' * 64
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker), \
             patch.object(self.manager, 'image_fingerprint', return_value='f' * 64), \
             patch.object(self.manager, 'check_compatibility'), patch.object(self.manager, 'dc'), \
             patch.object(self.manager, 'volume_import', side_effect=m.Failure('restore failure', 74)), \
             patch.object(self.manager, 'start') as start:
            with self.assertRaises(m.Failure) as ctx:
                self.manager.restore(path, 'test', True)
        self.assertEqual(ctx.exception.code, 74)
        self.assertTrue(self.manager.pending.exists())
        self.assertEqual(self.manager.load()['id'], 'baseline')
        start.assert_not_called()

    def test_restore_running_project_queries_scans_even_with_marker(self):
        path = self.backup_fixture()
        self.manager.write_pending('upgrade', 'target')
        def docker(*args, **kw):
            if args[:2] == ('image', 'inspect'):
                return 'sha256:' + 'c' * 64
            if args[0] == 'ps':
                return 'running-container'
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker), \
             patch.object(self.manager, 'image_fingerprint', return_value='f' * 64), \
             patch.object(self.manager, 'check_compatibility'), \
             patch.object(self.manager, 'scan_gate', side_effect=m.Failure('query unavailable', 76)) as gate, \
             patch.object(self.manager, 'dc') as dc:
            with self.assertRaises(m.Failure) as ctx:
                self.manager.restore(path, 'test', True)
        self.assertEqual(ctx.exception.code, 76)
        gate.assert_called_once()
        dc.assert_not_called()

    def test_image_save_uses_protected_tags(self):
        path = self.backup_fixture()
        saved = []
        def docker(*args, **kw):
            if args[:2] == ('image', 'save'):
                saved.extend(args[4:])
                (Path(args[3])).write_bytes((path / 'images.tar').read_bytes())
            return ''
        credentials = Path(self.manager.cfg['GMP_CREDENTIALS_FILE'])
        credentials.write_text('{}')
        credentials.chmod(0o600)
        tags = json.loads((path / 'backup.json').read_bytes())['image_tags']
        def export(image, volume, dest):
            with tarfile.open(dest, 'w'): pass
        with patch.object(self.manager, 'docker', side_effect=docker), \
             patch.object(self.manager, 'protect_images', return_value=tags), \
             patch.object(self.manager, 'image_fingerprint', return_value='f' * 64), \
             patch.object(self.manager, 'volume_export', side_effect=export):
            self.manager.snapshot(self.bundle)
        self.assertEqual(set(saved), set(tags.values()))
        self.assertTrue(all('@sha256:' not in image for image in saved))

    def test_resume_after_migration_uses_recorded_target_only(self):
        target = self.manager.load(self.manager.release_path('target'))
        self.manager.write_pending('upgrade', 'target', '/backup')
        self.manager.record_attempt(target)
        with patch.object(self.manager, 'docker', return_value=''), patch.object(self.manager, 'start') as start:
            self.manager.resume(True)
        self.assertEqual(self.manager.load()['id'], 'target')
        self.assertEqual(start.call_args.args[0], target['compose'])
        self.assertFalse(self.manager.pending.exists())

    def test_separate_copy_is_verified_before_completion(self):
        path = self.backup_fixture()
        with tempfile.TemporaryDirectory() as destination:
            self.manager.cfg['BACKUP_COPY_DIR'] = destination
            self.manager.copy_backup(path)
            copied = Path(destination) / path.name
            self.manager.verify_backup(copied)
            self.assertFalse((Path(destination) / ('.' + path.name + '.partial')).exists())

    def test_retention_preserves_upgrade_anchor(self):
        fixture = self.backup_fixture()
        parent = Path(self.manager.cfg['BACKUP_DIR'])
        parent.mkdir()
        for name in ('backup-01', 'backup-02', 'backup-03'):
            shutil.copytree(fixture, parent / name)
        self.manager.cfg['KEEP_BACKUPS'] = '1'
        m.atomic(self.manager.state / 'last-upgrade-backup', str(parent / 'backup-01').encode())
        self.manager.retention(parent / 'backup-03')
        self.assertTrue((parent / 'backup-01').exists())
        self.assertFalse((parent / 'backup-02').exists())
        self.assertTrue((parent / 'backup-03').exists())

    def test_version_report_uses_rolling_channels_and_never_deploys(self):
        bundle = copy.deepcopy(self.bundle)
        bundle['references'] = {s: v['image'] for s, v in bundle['compose']['services'].items()}
        m.atomic(self.manager.current, m.canonical(m.seal(bundle)))
        before = self.manager.current.read_bytes()
        with patch.object(m, 'urlopen', return_value=io.BytesIO(b'reviewed source')), \
             patch.object(self.manager, 'docker', return_value=json.dumps({'digest': 'sha256:' + 'a' * 64})) as docker:
            self.manager.version_check()
        for call in docker.call_args_list:
            self.assertEqual(call.args[:3], ('buildx', 'imagetools', 'inspect'))
            self.assertNotIn('@sha256:', call.args[3])
        self.assertEqual(before, self.manager.current.read_bytes())

    def test_version_report_after_restore_compares_original_repository_digests(self):
        bundle = copy.deepcopy(self.bundle)
        bundle['repository_digests'] = {s: v['image'] for s, v in bundle['compose']['services'].items()}
        bundle['restored_from'] = 'verified-backup'
        for service in bundle['compose']['services'].values():
            service['image'] = 'sha256:' + 'c' * 64
        m.atomic(self.manager.current, m.canonical(m.seal(bundle)))
        with patch.object(m, 'urlopen', return_value=io.BytesIO(b'reviewed source')), \
             patch.object(self.manager, 'docker', return_value=json.dumps({'digest': 'sha256:' + 'a' * 64})), \
             patch.object(self.manager, 'log') as log:
            self.manager.version_check()
        self.assertFalse(any('remote image differs' in call.args[0] for call in log.call_args_list))
