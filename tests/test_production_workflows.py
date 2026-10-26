import gzip
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_workflows as base
from test_maintenance import m


class ProductionWorkflowTests(unittest.TestCase):
    setUp = base.WorkflowTests.setUp
    backup_fixture = base.WorkflowTests.backup_fixture
    checksums = base.WorkflowTests.checksums

    def test_capacity_failure_leaves_only_good_backup_and_running_stack(self):
        backup = self.backup_fixture()
        before = sorted(backup.rglob('*'))
        for command in (lambda: self.manager.backup(True), lambda: self.manager.upgrade('target', True)):
            with patch.object(self.manager, 'scan_gate'), patch.object(self.manager, 'health_once'), \
                 patch.object(self.manager, 'check_compatibility'), patch.object(self.manager, 'pull'), \
                 patch.object(self.manager, 'protect_images'), \
                 patch.object(self.manager, 'capacity', side_effect=m.Failure('no space')), \
                 patch.object(self.manager, 'stop') as stop, patch.object(self.manager, 'retention') as retention:
                with self.assertRaises(m.Failure):
                    command()
            stop.assert_not_called()
            retention.assert_not_called()
        self.assertFalse(self.manager.pending.exists())
        self.assertEqual(sorted(backup.rglob('*')), before)

    def test_remote_probe_failure_happens_before_stop(self):
        self.manager.cfg['BACKUP_COPY_DIR'] = str(self.root)
        inventory = {'root': self.root, 'volumes': {}, 'image_bytes': 100,
                     'volume_bytes': {key: 100 for key in m.VOLUMES if key not in m.SOCKETS}}
        with patch.object(self.manager, 'scan_gate'), patch.object(self.manager, 'health_once'), \
             patch.object(self.manager, 'report_storage', return_value=inventory), \
             patch.object(self.manager, 'remote_target', side_effect=OSError('read only')), \
             patch.object(self.manager, 'stop') as stop:
            with self.assertRaises(OSError):
                self.manager.backup(True)
        stop.assert_not_called()
        self.assertFalse(self.manager.pending.exists())

    def test_gzip_and_legacy_archives_verify_and_import_with_real_tar(self):
        backup = self.backup_fixture()
        source = self.root / 'source'
        source.mkdir()
        (source / 'settings').write_bytes(b'essential-settings\n' * 65536)
        (source / 'settings').chmod(0o600)
        tags = json.loads((backup / 'backup.json').read_bytes())['image_tags']
        Path(self.manager.cfg['GMP_CREDENTIALS_FILE']).write_text('{}')
        Path(self.manager.cfg['GMP_CREDENTIALS_FILE']).chmod(0o600)
        restored = {}
        def docker(*args, **kwargs):
            if args[:2] == ('image', 'save'):
                shutil.copyfile(backup / 'images.tar', args[3])
            elif args[0] == 'run':
                if '/source' in args:
                    command = ['tar', '-C', str(source), *args[args.index('/source') + 1:]]
                    subprocess.run(command, stdout=kwargs['stdout'], check=True)
                else:
                    mount = args[args.index('--mount') + 1]
                    volume = mount.split('src=')[1].split(',')[0]
                    dest = self.root / volume
                    dest.mkdir(exist_ok=True)
                    command = ['tar', '-C', str(dest), *args[args.index('/dest') + 1:]]
                    subprocess.run(command, stdin=kwargs['stdin'], check=True)
                    restored[volume] = dest
            return ''
        for compression in ('none', 'gzip'):
            self.manager.cfg['BACKUP_COMPRESSION'] = compression
            with patch.object(self.manager, 'docker', side_effect=docker), \
                 patch.object(self.manager, 'protect_images', return_value=tags), \
                 patch.object(self.manager, 'image_fingerprint', return_value='f' * 64):
                snapshot = self.manager.snapshot(self.bundle)
                self.manager.verify_backup(snapshot)
                for key in m.VOLUMES:
                    if key not in m.SOCKETS:
                        self.manager.volume_import('test-image', compression + key,
                                                   self.manager.volume_archive(snapshot, key))
                        self.assertEqual((restored[compression + key] / 'settings').read_bytes(),
                                         (source / 'settings').read_bytes())
                        self.assertEqual((restored[compression + key] / 'settings').stat().st_mode & 0o777, 0o600)
            if compression == 'gzip':
                path = self.manager.volume_archive(snapshot, 'psql_data_vol')
                path.write_bytes(b'not a gzip or tar')
                self.checksums(snapshot)
                with self.assertRaises(m.Failure):
                    self.manager.verify_backup(snapshot)
        # Old metadata without volume_compression remains supported.
        self.manager.verify_backup(backup)

    def test_gzip_restore_selects_new_volumes_and_keeps_old(self):
        backup = self.backup_fixture()
        metadata = json.loads((backup / 'backup.json').read_bytes())
        metadata['volume_compression'] = 'gzip'
        (backup / 'backup.json').write_bytes(m.canonical(metadata))
        for path in (backup / 'volumes').iterdir():
            path.with_suffix('.tar.gz').write_bytes(gzip.compress(path.read_bytes()))
            path.unlink()
        self.checksums(backup)
        created, imported = [], []
        def docker(*args, **kwargs):
            if args[:2] == ('image', 'inspect'):
                return 'sha256:' + 'c' * 64
            if args[:2] == ('volume', 'create'):
                created.append(args[2])
            return ''
        def volume_import(image, volume, path):
            self.assertTrue(str(path).endswith('.tar.gz'))
            imported.append(volume)
        with patch.object(self.manager, 'docker', side_effect=docker), \
             patch.object(self.manager, 'image_fingerprint', return_value='f' * 64), \
             patch.object(self.manager, 'capacity'), patch.object(self.manager, 'stop'), \
             patch.object(self.manager, 'check_compatibility'), patch.object(self.manager, 'dc'), \
             patch.object(self.manager, 'start'), patch.object(self.manager, 'protect_images'), \
             patch.object(self.manager, 'volume_import', side_effect=volume_import):
            self.manager.restore(backup, 'test', True)
        self.assertEqual(len(imported), len(m.VOLUMES) - len(m.SOCKETS))
        self.assertEqual(len(created), len(m.VOLUMES))
        self.assertTrue(all('_restore_' in volume for volume in created))
        self.assertEqual(self.bundle['compose']['volumes']['psql_data_vol']['name'], 'test_psql_data_vol')
        self.assertFalse(self.manager.pending.exists())

    def test_remote_detach_cannot_redirect_copy_into_fallback_directory(self):
        backup = self.backup_fixture()
        remote = self.root / 'remote'
        remote.mkdir()
        detached = self.root / 'detached'
        self.manager.cfg['BACKUP_COPY_DIR'] = str(remote)
        calls = 0
        def mount_info(path):
            nonlocal calls
            calls += 1
            if calls == 6:
                remote.rename(detached)
                remote.mkdir()
            return {'target': str(remote), 'source': 'server:/backup'}
        with patch.object(self.manager, 'mount_info', side_effect=mount_info):
            with self.assertRaises(m.Failure):
                self.manager.copy_backup(backup)
        self.assertEqual(list(remote.iterdir()), [])
        self.assertTrue((detached / ('.' + backup.name + '.partial')).exists())
        self.assertFalse((detached / backup.name).exists())

    def test_image_save_timeout_keeps_partial_and_recovery_marker(self):
        self.manager.write_pending('backup')
        Path(self.manager.cfg['GMP_CREDENTIALS_FILE']).write_text('{}')
        Path(self.manager.cfg['GMP_CREDENTIALS_FILE']).chmod(0o600)
        def docker(*args, **kwargs):
            if args[:2] == ('image', 'save'):
                raise m.Failure('archive timeout')
            return ''
        with patch.object(self.manager, 'docker', side_effect=docker), \
             patch.object(self.manager, 'protect_images', return_value={'image': 'tag'}), \
             patch.object(self.manager, 'image_fingerprint', return_value='f' * 64):
            with self.assertRaises(m.Failure):
                self.manager.snapshot(self.bundle)
        self.assertTrue(self.manager.pending.exists())
        self.assertTrue(list(Path(self.manager.cfg['BACKUP_DIR']).glob('.*.partial')))
        self.assertFalse(list(Path(self.manager.cfg['BACKUP_DIR']).glob('backup-*')))

    def test_success_timestamp_written_only_on_finished_operation(self):
        self.manager.write_pending('feed-update')
        path = self.manager.state / 'last-success.json'
        self.assertFalse(path.exists())
        self.manager.finish(self.bundle)
        self.assertEqual(json.loads(path.read_bytes())['feed-update']['release'], 'baseline')

    def test_resume_does_not_claim_failed_backup_completed(self):
        self.manager.write_pending('backup', 'baseline')
        with patch.object(self.manager, 'docker', return_value=''), patch.object(self.manager, 'start'):
            self.manager.resume(True)
        successes = json.loads((self.manager.state / 'last-success.json').read_bytes())
        self.assertNotIn('backup', successes)
        self.assertIn('resume', successes)
