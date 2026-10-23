import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("maintenance", ROOT / "lib/maintenance.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def compose():
    services = {}
    for name in m.SERVICES:
        repo = m.IMAGE_REPOS.get(name, name)
        services[name] = {"image": m.REGISTRY + repo + "@sha256:" + "a" * 64,
                          "volumes": []}
        if name in m.FEEDS or name == "gsa":
            services[name]["environment"] = {"KEEP_ALIVE": "1"}
    for name in ("vulnerability-tests", "data-objects", "report-formats"):
        services[name]["environment"]["FEED_RELEASE"] = "24.10"
    for name, volume in (("pg-gvm", "psql_data_vol"), ("gvmd", "gvmd_data_vol")):
        services[name]["volumes"] = [{"type": "volume", "source": volume,
                                       "target": "/data"}]
    return {"services": services, "volumes": {v: {"name": "test_" + v}
                                              for v in m.VOLUMES}}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "update.env"
        self.config.write_text(f'DOWNLOAD_DIR="{self.root}/state"\nPROJECT="test"\n'
                               f'BACKUP_DIR="{self.root}/backups"\n'
                               f'LOCK_FILE="{self.root}/lock"\n'
                               f'LOG="{self.root}/log"\nMIN_FREE_MB="1"\n')
        self.config.chmod(0o600)
        self.manager = m.Manager(m.read_config(self.config), dry_run=False)

    def test_config_does_not_execute_shell(self):
        self.config.write_text('DOWNLOAD_DIR="$(touch /tmp/gb-must-not-exist)"\nPROJECT=x\n')
        with self.assertRaises(m.Failure):
            m.read_config(self.config)

    def test_insecure_config_rejected(self):
        self.config.chmod(0o644)
        with self.assertRaises(m.Failure):
            m.read_config(self.config)

    def test_invalid_retention_rejected(self):
        with self.config.open("a") as f:
            f.write('KEEP_BACKUPS="0"\n')
        with self.assertRaises(m.Failure):
            m.read_config(self.config)

    def test_subprocess_failure_is_not_lost(self):
        with self.assertRaises(m.Failure) as ctx:
            self.manager.run(["bash", "-c", "echo password=private >&2; exit 31"])
        self.assertNotIn("private", str(ctx.exception))

    def test_lock_conflict(self):
        with self.manager.lock():
            with self.assertRaises(m.Failure) as ctx:
                with self.manager.lock():
                    pass
        self.assertEqual(ctx.exception.code, 75)

    def test_release_rejects_unknown_service_and_bind(self):
        c = compose()
        m.validate_compose(c, pinned=True)
        c["services"]["surprise"] = {"image": "anything"}
        with self.assertRaises(m.Failure):
            m.validate_compose(c, pinned=True)
        c = compose()
        c["services"]["gvmd"]["volumes"] = [{"type": "bind", "source": "/etc"}]
        with self.assertRaises(m.Failure):
            m.validate_compose(c, pinned=True)

    def test_unknown_feed_fails_closed(self):
        c = compose()
        c["services"]["data-objects"]["environment"]["FEED_RELEASE"] = "25.0"
        with self.assertRaises(m.Failure):
            m.validate_compose(c, pinned=True)

    def test_dry_run_writes_nothing(self):
        manager = m.Manager(m.read_config(self.config), dry_run=True)
        before = sorted(self.root.rglob("*"))
        with patch.object(manager, "run", side_effect=AssertionError("Docker was called")):
            manager.execute(m.arguments(["feed-update", "--dry-run"]))
        self.assertEqual(before, sorted(self.root.rglob("*")))

    def test_incomplete_operation_blocks_automation(self):
        self.manager.state.mkdir(parents=True)
        self.manager.pending.write_text('{}')
        with self.assertRaises(m.Failure) as ctx:
            self.manager.require_clean()
        self.assertEqual(ctx.exception.code, 70)

    def test_pull_failure_prevents_stop(self):
        with patch.object(self.manager, "run", side_effect=m.Failure("pull failed")), \
                patch.object(self.manager, "stop") as stop:
            with self.assertRaises(m.Failure):
                self.manager.pull(compose())
        stop.assert_not_called()

    def test_deploy_failure_keeps_recovery_marker_no_automatic_restore(self):
        self.manager.state.mkdir(parents=True)
        self.manager.write_pending("upgrade", "test-target")
        with patch.object(self.manager, "dc", side_effect=m.Failure("migration failed")), \
                patch.object(self.manager, "restore") as restore:
            with self.assertRaises(m.Failure):
                self.manager.start(compose())
        self.assertTrue(self.manager.pending.exists())
        restore.assert_not_called()

    def test_health_timeout_keeps_recovery_marker(self):
        self.manager.state.mkdir(parents=True)
        self.manager.write_pending("upgrade", "test-target")
        with patch.object(self.manager, "health_once", side_effect=m.Failure("not ready")), \
                patch.object(m.time, "monotonic", side_effect=[0, 999999]):
            with self.assertRaises(m.Failure):
                self.manager.health(compose())
        self.assertTrue(self.manager.pending.exists())

    def test_corrupt_backup_rejected_before_docker(self):
        b = self.root / "bad-backup"
        b.mkdir()
        (b / "checksums.json").write_text(json.dumps({"images.tar": "0" * 64}))
        (b / "images.tar").write_bytes(b"corrupt")
        with patch.object(self.manager, "run") as run:
            with self.assertRaises(m.Failure):
                self.manager.verify_backup(b)
        run.assert_not_called()

    def test_missing_backup_and_invalid_metadata_have_backup_exit_code(self):
        for path in (self.root / 'missing', self.root):
            with self.assertRaises(m.Failure) as ctx:
                self.manager.verify_backup(path)
            self.assertEqual(ctx.exception.code, 74)

    def test_restore_requires_exact_confirmation(self):
        with patch.object(self.manager, "verify_backup") as verify:
            with self.assertRaises(m.Failure):
                self.manager.restore(self.root, "wrong", False)
        verify.assert_not_called()

    def test_normal_one_shot_exit_is_healthy(self):
        c = compose()
        states = {s: {"Status": "running", "ExitCode": 0,
                      "Health": {"Status": "healthy"}} for s in m.SERVICES}
        for s in m.INITIALIZERS:
            states[s] = {"Status": "exited", "ExitCode": 0}
        m.validate_states(c, states)
        states["pg-gvm-migrator"]["ExitCode"] = 1
        with self.assertRaises(m.Failure):
            m.validate_states(c, states)

    def test_upgrade_preserves_live_volume_names(self):
        old = compose()
        new = compose()
        new['volumes']['psql_data_vol']['name'] = 'different_database'
        m.preserve_volumes(old, new)
        self.assertEqual(new['volumes'], old['volumes'])

    def test_wrong_running_mount_is_rejected(self):
        c = compose()
        containers = [{'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                       'Mounts': [{'Type': 'volume', 'Name': 'wrong_db', 'Destination': '/data'}]}]
        with self.assertRaises(m.Failure):
            m.validate_mounts(c, containers)

    def test_running_image_drift_is_rejected(self):
        c = compose()
        container = {'Config': {'Labels': {'com.docker.compose.service': 'pg-gvm'}},
                     'Image': 'sha256:' + 'f' * 64,
                     'Mounts': [{'Type': 'volume', 'Name': 'test_psql_data_vol', 'Destination': '/data'}],
                     'State': {'Status': 'running', 'ExitCode': 0}}
        def docker(*args, **kwargs):
            if args[0] == 'inspect':
                return json.dumps([container])
            return json.dumps([{'Id': 'sha256:' + 'a' * 64} for _ in args[2:]])
        with patch.object(self.manager, 'dc', return_value='container-id'), \
             patch.object(self.manager, 'docker', side_effect=docker):
            with self.assertRaises(m.Failure):
                self.manager.states(c)

    def test_remote_docker_endpoint_rejected_before_commands(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://remote:2375'}), \
             patch.object(self.manager, 'docker') as docker:
            with self.assertRaises(m.Failure):
                self.manager.preflight()
        docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
