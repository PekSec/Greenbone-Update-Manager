import importlib.util
from pathlib import Path
import unittest
import tempfile
from xml.etree import ElementTree as ET

spec = importlib.util.spec_from_file_location("probe", Path(__file__).resolve().parents[1] / "lib/gmp_probe.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def tasks(status="Done", schedule="", count=1):
    return ET.fromstring(f'<get_tasks_response status="200"><task id="a"><status>{status}</status>'
                         f'<schedule id="{schedule}"/></task><task_count><filtered>{count}</filtered>'
                         '</task_count></get_tasks_response>')


class ProbeTests(unittest.TestCase):
    def test_active_queued_unknown_and_scheduled_tasks_block(self):
        for state in ("Running", "Requested", "Queued", "Stop Requested", "Processing", "Unknown"):
            self.assertFalse(p.tasks_idle(tasks(state)))
        self.assertFalse(p.tasks_idle(tasks(schedule="assigned-schedule")))
        self.assertTrue(p.tasks_idle(tasks()))

    def test_query_error_or_pagination_fails_closed(self):
        with self.assertRaises(p.ProbeError):
            p.tasks_idle(tasks(count=200))
        with self.assertRaises(p.ProbeError):
            p.tasks_idle(ET.fromstring('<get_tasks_response status="403"/>'))

    def test_super_admin_required(self):
        response = ET.fromstring('<get_users_response status="200"><user><name>ops</name>'
                                 '<role><name>Admin</name></role></user></get_users_response>')
        with self.assertRaises(p.ProbeError):
            p.require_super_admin(response, "ops")
        response.find("user/role/name").text = "Super Admin"
        p.require_super_admin(response, "ops")

    def test_feed_not_loaded_or_syncing_fails(self):
        response = ET.fromstring('<get_feeds_response status="200">' + ''.join(
            f'<feed><type>{kind}</type><version>202610230100</version></feed>'
            for kind in ("NVT", "CERT", "SCAP", "GVMD_DATA")) + '</get_feeds_response>')
        p.feeds_ready(response)
        ET.SubElement(response.find("feed"), "currently_syncing")
        with self.assertRaises(p.ProbeError):
            p.feeds_ready(response)

    def test_stale_database_feed_is_not_ready(self):
        expected = {'nvt': '202610230100', 'scap': '1792713600', 'cert': '1792713600',
                    'objects': {'config': {'test-id': 100}}}
        loaded = {'nvt': '202610220100', 'scap': '1792713600', 'cert': '1792713600',
                  'config': {'test-id': 100}}
        with self.assertRaises(p.ProbeError):
            p.database_ready(expected, loaded)
        loaded['nvt'] = expected['nvt']
        p.database_ready(expected, loaded)
        loaded['config']['test-id'] = 99
        with self.assertRaises(p.ProbeError):
            p.database_ready(expected, loaded)


if __name__ == "__main__":
    unittest.main()
