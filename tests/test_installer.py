import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('sbin', 'config', 'data', 'systemd', 'cron', 'logrotate', 'fake'):
            (self.root / name).mkdir()
        self.env = dict(os.environ, SBIN_DIR=str(self.root / 'sbin'), CONFIG_DIR=str(self.root / 'config'),
                        DATA_DIR=str(self.root / 'data'), LOG_FILE=str(self.root / 'log'),
                        LOCK_FILE=str(self.root / 'data/maintenance.lock'), RUN_USER=os.environ['USER'],
                        SYSTEMD_DIR=str(self.root / 'systemd'), CRON_DIR=str(self.root / 'cron'),
                        LOGROTATE_DIR=str(self.root / 'logrotate'), MOCK_ROOT=str(self.root),
                        PATH=str(self.root / 'fake') + ':' + os.environ['PATH'])
        self.fake('id', '#!/bin/bash\nif [[ "$*" == -u ]]; then echo 0; else exec /usr/bin/id "$@"; fi\n')
        self.fake('systemctl', '#!/bin/bash\nprintf "%s\\n" "$*" >> "$MOCK_ROOT/systemctl-calls"\n'
                               '[[ "$1" != is-active ]] || exit 3\n')
        self.fake('crontab', '#!/bin/bash\nif [[ "$1" == -u ]]; then shift 2; fi\n'
                             'if [[ "$1" == -l ]]; then cat "$MOCK_ROOT/user-cron"; else cat > "$MOCK_ROOT/user-cron"; fi\n')
        (self.root / 'user-cron').write_text('0 1 * * * echo keep-me\n# greenbone-update (managed by install.sh)\n'
                                           '0 3 * * * /old/greenbone-update.sh\n')

    def fake(self, name, content):
        path = self.root / 'fake' / name
        path.write_text(content)
        path.chmod(0o755)

    def run_install(self, *args):
        return subprocess.run(['bash', str(ROOT / 'install.sh'), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def test_legacy_timer_removed_and_three_safe_jobs_installed(self):
        (self.root / 'systemd/greenbone-update.timer').touch()
        result = self.run_install('--scheduler', 'systemd')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'systemd/greenbone-update.timer').exists())
        self.assertIn('disable --now greenbone-update.timer', (self.root / 'systemctl-calls').read_text())
        self.assertEqual(len(list((self.root / 'systemd').glob('*.timer'))), 3)
        for command, unit in [('feed-update', 'feed'), ('backup', 'backup'), ('version-check', 'version-check')]:
            content = (self.root / f'systemd/greenbone-{unit}.service').read_text()
            self.assertIn('greenbone-update.sh ' + command, content)
            self.assertNotIn('@', content)
        config = self.root / 'config/update.env'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertIn('keep-me', (self.root / 'user-cron').read_text())
        self.assertNotIn('/old/greenbone-update.sh', (self.root / 'user-cron').read_text())

    def test_installer_dry_run_changes_no_files(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.run_install('--scheduler', 'systemd', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_active_lock_prevents_tool_replacement(self):
        installed = self.root / 'sbin/greenbone-update.sh'
        installed.write_text('original tool')
        with (self.root / 'data/maintenance.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_install('--scheduler', 'none')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(installed.read_text(), 'original tool')

    def test_cron_preserved_with_separate_commands(self):
        result = self.run_install('--scheduler', 'cron')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (self.root / 'cron/greenbone-maintenance').read_text()
        self.assertEqual(content.count('greenbone-update.sh '), 3)
        self.assertNotIn(' upgrade', content)
        self.assertIn(' feed-update', content)

    def test_uninstall_keeps_backup_data_and_refuses_purge(self):
        backup = self.root / 'data/backups/precious'
        backup.parent.mkdir()
        backup.write_text('keep')
        result = subprocess.run(['bash', str(ROOT / 'uninstall.sh')], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(backup.exists())
        result = subprocess.run(['bash', str(ROOT / 'uninstall.sh'), '--purge'], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 64)
        self.assertTrue(backup.exists())
