#!/usr/bin/env python3
"""Runs inside the pinned official gvm-tools image; credentials arrive on stdin."""
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET


class ProbeError(Exception):
    pass


def response(value):
    root = ET.fromstring(value) if isinstance(value, (str, bytes)) else value
    if root.get("status") != "200":
        raise ProbeError("GMP request failed")
    return root


def complete(root, element):
    root = response(root)
    items = root.findall(element)
    count = root.findtext(element + "_count/filtered")
    if count is None or not count.isdigit() or int(count) != len(items):
        raise ProbeError("Incomplete GMP inventory")
    return items


def require_super_admin(value, username):
    users = response(value).findall("user")
    matches = [u for u in users if u.findtext("name") == username]
    if len(matches) != 1 or "Super Admin" not in [r.findtext("name") for r in matches[0].findall("role")]:
        raise ProbeError("Maintenance requires a Super Admin with visibility of all users' tasks")


def tasks_idle(value):
    for task in complete(value, "task"):
        if task.findtext("status") not in ("New", "Done", "Stopped", "Interrupted"):
            return False
        schedule = task.find("schedule")
        if schedule is not None and schedule.get("id", "") not in ("", "0", "00000000-0000-0000-0000-000000000000"):
            return False
    return True


def feeds_ready(value):
    root = response(value)
    for field in ("feed_owner_set", "feed_resources_access"):
        if root.find(field) is not None and root.findtext(field) != "1":
            raise ProbeError("Feed ownership/access is not configured")
    feeds = {f.findtext("type"): f for f in root.findall("feed")}
    if not {"NVT", "CERT", "SCAP", "GVMD_DATA"}.issubset(feeds):
        raise ProbeError("Incomplete feed inventory")
    for feed in feeds.values():
        if not re.fullmatch(r"[0-9]{8,14}", feed.findtext("version", "")):
            raise ProbeError("Feed has not been imported")
        if feed.find("currently_syncing") is not None:
            raise ProbeError("Feed import is still running")
        if feed.find("sync_not_available") is not None:
            raise ProbeError("Feed status cannot be determined")
    return feeds


def expected_data(base=Path('/maintenance-feeds')):
    plugin = (base / 'vt/plugin_feed_info.inc').read_text()
    match = re.search(r'PLUGIN_SET\s*=\s*"([0-9]+)"', plugin)
    if not match:
        raise ProbeError('Missing VT feed version')
    result = {'nvt': match[1], 'objects': {}}
    for kind in ('scap', 'cert'):
        stamp = (base / kind / 'timestamp').read_text().strip()[:8]
        result[kind] = str(int(datetime.strptime(stamp, '%Y%m%d').replace(tzinfo=timezone.utc).timestamp()))
    for directory, element in (('scan-configs', 'config'), ('port-lists', 'port_list'), ('report-formats', 'report_format')):
        objects = {}
        for path in (base / 'objects').rglob('*.xml'):
            if directory not in path.parts:
                continue
            root = ET.parse(path).getroot()
            entry = root if root.tag == element else root.find('.//' + element)
            if entry is None or entry.findtext('deprecated') == '1':
                continue
            identity = entry.get('id', '')
            if not re.fullmatch(r'[a-f0-9-]{36}', identity):
                raise ProbeError('Unknown feed object layout')
            objects[identity] = int(path.stat().st_mtime)
        if not objects:
            raise ProbeError('Feed object files are missing')
        result['objects'][element] = objects
    return result


def database_ready(expected, loaded):
    for key in ('nvt', 'scap', 'cert'):
        if str(loaded.get(key)) != expected[key]:
            raise ProbeError('Database feed version differs from deployed data')
    for kind, objects in expected['objects'].items():
        for identity, modified in objects.items():
            if int(loaded.get(kind, {}).get(identity, -1)) < modified:
                raise ProbeError('Feed object import is incomplete')


def check(gmp, mode, username, loaded=None):
    response(gmp.get_version())
    if mode == "idle":
        require_super_admin(gmp.get_users(filter_string="rows=-1"), username)
        return {"idle": tasks_idle(gmp.get_tasks(filter_string="rows=-1", details=True))}
    scanners = complete(response(gmp.get_scanners(filter_string="rows=-1", details=True)), "scanner")
    openvas = [s for s in scanners if s.findtext("type") == "2"]
    if not openvas:
        raise ProbeError("No OpenVAS scanner registered")
    for scanner in openvas:
        response(gmp.verify_scanner(scanner.get("id")))
    feeds = feeds_ready(gmp.get_feeds())
    expected = expected_data()
    if feeds['NVT'].findtext('version') != expected['nvt']:
        raise ProbeError('Scanner has not loaded the deployed VT data')
    database_ready(expected, loaded or {})
    for method, element in ((gmp.get_scan_configs, "config"), (gmp.get_port_lists, "port_list"),
                            (gmp.get_report_formats, "report_format")):
        if not response(method(filter_string="rows=1")).findall(element):
            raise ProbeError("Required feed resources have not been imported")
    return {"healthy": True}


def main():
    try:
        from gvm.connections import UnixSocketConnection
        from gvm.protocols.gmp import GMP
        credentials = json.load(sys.stdin)
        connection = UnixSocketConnection(path="/run/gvmd/gvmd.sock", timeout=60)
        with GMP(connection=connection) as gmp:
            response(gmp.authenticate(credentials["username"], credentials["password"]))
            result = check(gmp, sys.argv[1], credentials["username"], credentials.get('database'))
        print(json.dumps(result))
        return 0
    except Exception:
        # Never emit library exceptions: they can contain XML credentials or scan data.
        print("GMP probe failed: verify credentials, permissions, scanner and feed readiness", file=sys.stderr)
        return 76


if __name__ == "__main__":
    sys.exit(main())
