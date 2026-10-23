#!/usr/bin/env python3
"""Single-VM Greenbone maintenance. Host dependencies: Python stdlib and Docker."""
import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.request import urlopen

REGISTRY = "registry.community.greenbone.net/community/"
FEEDS = ("notus-data", "vulnerability-tests", "scap-data", "dfn-cert-data",
         "cert-bund-data", "report-formats", "data-objects")
INITIALIZERS = ("gpg-data", "gvm-config", "configure-openvas", "pg-gvm-migrator")
DAEMONS = ("redis-server", "pg-gvm", "gvmd", "gsad", "nginx", "ospd-openvas", "openvas", "openvasd")
SERVICES = FEEDS + INITIALIZERS + DAEMONS + ("gsa", "gvm-tools")
IMAGE_REPOS = {"configure-openvas": "openvas-scanner", "openvas": "openvas-scanner", "openvasd": "openvas-scanner"}
LATEST = set(FEEDS) | {'gpg-data', 'gvm-config', 'nginx', 'redis-server', 'gvm-tools'}
TRACKING_REFERENCES = {name: REGISTRY + IMAGE_REPOS.get(name, name) + ':' +
                       ('latest' if name in LATEST else 'stable-slim' if name == 'gsa' else 'stable')
                       for name in SERVICES}
VOLUMES = ("gpg_data_vol", "scap_data_vol", "cert_data_vol", "data_objects_vol", "gvmd_data_vol",
           "psql_data_vol", "vt_data_vol", "notus_data_vol", "psql_socket_vol", "gvmd_socket_vol",
           "ospd_openvas_socket_vol", "redis_socket_vol", "openvas_data_vol", "openvas_log_data_vol",
           "gsa_data_vol", "nginx_config_vol", "nginx_certificates_vol")
SOCKETS = {"psql_socket_vol", "gvmd_socket_vol", "ospd_openvas_socket_vol", "redis_socket_vol"}
ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
SOURCE_URL = "https://greenbone.github.io/docs/latest/_static/compose.yaml"


class Failure(Exception):
    def __init__(self, message, code=69):
        super().__init__(message)
        self.code = code


def require(ok, message, code=69):
    if not ok:
        raise Failure(message, code)


def private_file(path):
    path = Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            "Sensitive file must be regular, owned by this user and mode 0600", 64)
    return path


def read_config(path):
    require("@" not in str(path), "Unrendered entry point: run install.sh or set GB_CONFIG_FILE", 64)
    config = {}
    for line in private_file(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        require(re.match(r"^[A-Z][A-Z0-9_]*=", line), "Config accepts literal KEY=value assignments only", 64)
        key, value = line.split("=", 1)
        require(not any(x in value for x in ("$", "`", "\n", "\r")), "Shell expansion is not supported in config", 64)
        values = shlex.split(value, comments=True)
        require(len(values) <= 1, "Quote configuration values containing spaces", 64)
        config[key] = values[0] if values else ""
    for key in ("DOWNLOAD_DIR", "PROJECT"):
        require(config.get(key), "Missing configuration: " + key, 64)
    require(ID.fullmatch(config["PROJECT"]), "Invalid PROJECT", 64)
    base = config["DOWNLOAD_DIR"]
    defaults = {"BACKUP_DIR": base + "/backups", "LOCK_FILE": base + "/maintenance.lock",
                "LOG": base + "/maintenance.log", "KEEP_BACKUPS": "7", "HEALTH_TIMEOUT": "3600",
                "COMMAND_TIMEOUT": "1800", "MIN_FREE_MB": "15000", "MAX_LOG_BYTES": "20971520",
                "GMP_CREDENTIALS_FILE": str(Path(path).parent / "gmp-credentials.json"),
                "BACKUP_COPY_DIR": "", "AUTO_WINDOW_UTC": "", "AUTO_WINDOW_ACK": "0",
                "COMPOSE_URL": SOURCE_URL, "DOCKER_BIN": "docker"}
    for key, value in defaults.items():
        config.setdefault(key, value)
    for key in ("KEEP_BACKUPS", "HEALTH_TIMEOUT", "COMMAND_TIMEOUT", "MIN_FREE_MB", "MAX_LOG_BYTES"):
        require(config[key].isdigit() and 0 < int(config[key]) <= 10**9, "Invalid positive integer: " + key, 64)
    for key in ("DOWNLOAD_DIR", "BACKUP_DIR", "LOCK_FILE", "LOG", "GMP_CREDENTIALS_FILE", "BACKUP_COPY_DIR"):
        if config[key]:
            p = Path(config[key])
            require(p.is_absolute() and str(p) != "/" and not p.is_symlink(),
                    "Expected an absolute non-symlink path: " + key, 64)
    if config["AUTO_WINDOW_UTC"]:
        require(re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]-(?:[01][0-9]|2[0-3]):[0-5][0-9]",
                             config["AUTO_WINDOW_UTC"]), "Invalid AUTO_WINDOW_UTC", 64)
    require(config["AUTO_WINDOW_ACK"] in ("0", "1"), "Invalid AUTO_WINDOW_ACK", 64)
    require(config["COMPOSE_URL"].startswith("https://greenbone.github.io/"), "Invalid COMPOSE_URL host", 64)
    config["CONFIG_FILE"] = str(Path(path).absolute())
    return config


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validate_compose(c, pinned, allow_local=False):
    require(set(c.get("services", {})) == set(SERVICES), "Unsupported service layout; review upstream architecture")
    require(set(c.get("volumes", {})) == set(VOLUMES), "Unsupported volume layout")
    require(not c.get("secrets") and not c.get("configs"), "External secrets/config mounts need a backup adapter")
    for name, service in c["services"].items():
        image = service.get("image", "")
        repo = REGISTRY + IMAGE_REPOS.get(name, name)
        local = allow_local and DIGEST.fullmatch(image)
        require(local or image.split("@", 1)[0].split(":", 1)[0] == repo, "Unexpected image repository for " + name)
        if pinned:
            require(local or ("@" in image and DIGEST.fullmatch(image.split("@")[-1])), "Image is not pinned: " + name)
        for key in ("build", "env_file", "secrets", "configs", "devices", "volumes_from", "container_name", "profiles"):
            require(not service.get(key), "Unsupported service field: " + key)
        for key in service.get("environment", {}):
            require(not re.search(r"password|passwd|secret|token|api.?key", key, re.I), "Keep credentials outside Compose environment")
        for mount in service.get("volumes", []):
            require(isinstance(mount, dict) and mount.get("type") == "volume" and mount.get("source") in VOLUMES,
                    "Only declared named volumes can be backed up")
    for name, volume in c["volumes"].items():
        require(not volume.get("external") and not volume.get("driver_opts") and volume.get("driver", "local") == "local",
                "Only local Docker volumes are supported")
        require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", volume.get("name", "")), "Volume name missing or unsafe: " + name)
    for name in ("vulnerability-tests", "data-objects", "report-formats"):
        require(c["services"][name].get("environment", {}).get("FEED_RELEASE") == "24.10",
                "Unknown feed compatibility; only reviewed FEED_RELEASE=24.10 is supported")


def seal(bundle):
    bundle = copy.deepcopy(bundle)
    bundle.pop("checksum", None)
    bundle["checksum"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def validate_bundle(bundle):
    require(bundle.get("schema") == 1 and ID.fullmatch(bundle.get("id", "")), "Invalid release bundle")
    require(seal(bundle)["checksum"] == bundle.get("checksum"), "Release bundle checksum mismatch")
    validate_compose(bundle["compose"], pinned=True, allow_local=bool(bundle.get("restored_from")))
    require(bundle.get("feed_release") == "24.10" and bundle.get("gvmd_major") in (25, 26),
            "Feed/manager compatibility is unknown; application upgrade must be reviewed")
    require(set(bundle.get("references", {})) == set(SERVICES), "Incomplete release reference inventory")
    return bundle


def preserve_volumes(old, new):
    require(set(old["volumes"]) == set(new["volumes"]), "Volume layout changes need a migration adapter")
    new["volumes"] = copy.deepcopy(old["volumes"])


def validate_mounts(c, containers):
    for container in containers:
        labels = container.get("Config", {}).get("Labels", {})
        name = labels.get("com.docker.compose.service")
        if name not in c["services"] or labels.get("com.docker.compose.oneoff", "False").lower() == "true":
            continue
        expected = {v["target"]: c["volumes"][v["source"]]["name"] for v in c["services"][name].get("volumes", [])}
        actual = {v["Destination"]: v.get("Name") for v in container.get("Mounts", []) if v["Type"] == "volume"}
        require(actual == expected and not any(v["Type"] == "bind" for v in container.get("Mounts", [])),
                "Running volume mounts differ from the release: " + name)


def validate_states(c, states):
    for name in SERVICES:
        if name == "gvm-tools":
            continue
        state = states.get(name, {})
        status = state.get("Status")
        health = state.get("Health", {}).get("Status")
        if name in INITIALIZERS:
            ok = status == "exited" and state.get("ExitCode") == 0
        elif name in FEEDS or name == "gsa":
            keep_alive = str(c["services"][name].get("environment", {}).get("KEEP_ALIVE", "0"))
            ok = (status == "running" and health == "healthy") if keep_alive == "1" else (
                status == "exited" and state.get("ExitCode") == 0)
        else:
            ok = status == "running" and health not in ("starting", "unhealthy")
        require(ok, "Service is not ready: " + name, 73)


class Manager:
    def __init__(self, config, dry_run=False):
        self.cfg = config
        self.state = Path(config["DOWNLOAD_DIR"])
        self.current = self.state / "current.json"
        self.pending = self.state / "incomplete-operation.json"
        self.stage = "init"
        self.dry_run = dry_run

    def log(self, message):
        line = datetime.now(timezone.utc).isoformat(timespec="seconds") + " [" + self.stage + "] " + message
        print(line, flush=True)
        if not self.dry_run:
            path = Path(self.cfg["LOG"])
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            require(not path.is_symlink(), "Log path may not be a symlink", 64)
            if path.exists() and path.stat().st_size > int(self.cfg["MAX_LOG_BYTES"]):
                os.replace(path, str(path) + ".1")
            with path.open("a") as f:
                os.chmod(path, 0o600)
                f.write(line + "\n")

    def run(self, command, input=None, stdout=None, timeout=None, allowed=(0,), stdin=None):
        try:
            result = subprocess.run(command, input=input, stdin=stdin, stdout=stdout or subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout or int(self.cfg["COMMAND_TIMEOUT"]), check=False)
        except subprocess.TimeoutExpired:
            raise Failure("Command timed out during " + self.stage) from None
        except OSError:
            raise Failure("Could not execute command during " + self.stage) from None
        # Raw command output/arguments may contain credentials; never log them.
        require(result.returncode in allowed, "Command failed during " + self.stage + " (exit " + str(result.returncode) + ")")
        return (result.stdout or b"").decode(errors="replace")

    def docker(self, *args, **kwargs):
        name = 'gb-maint-' + uuid.uuid4().hex if args and args[0] == 'run' else None
        command = [self.cfg['DOCKER_BIN'], *args]
        if name:
            command[2:2] = ['--name', name]
        try:
            return self.run(command, **kwargs)
        except Failure:
            if name:
                self.run([self.cfg['DOCKER_BIN'], 'rm', '-f', name], allowed=(0, 1), timeout=30)
            raise

    def dc(self, c, *args, **kwargs):
        with tempfile.TemporaryDirectory(prefix="gb-compose-") as temp:
            path = Path(temp) / "compose.json"
            path.write_bytes(canonical(c))
            path.chmod(0o600)
            return self.docker("compose", "--env-file", "/dev/null", "--project-directory", str(self.state),
                               "-p", self.cfg["PROJECT"], "-f", str(path), *args, **kwargs)

    @contextmanager
    def lock(self):
        path = Path(self.cfg["LOCK_FILE"])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("Another maintenance operation holds the lock", 75) from None
            yield

    def require_clean(self):
        require(not self.pending.exists(), "Incomplete operation detected. Inspect status and explicitly restore a verified backup", 70)

    def write_pending(self, operation, target="", backup=""):
        atomic(self.pending, canonical({"operation": operation, "target": target, "backup": str(backup),
                                        "phase": "pre-data",
                                        "time": datetime.now(timezone.utc).isoformat()}))

    def record_attempt(self, bundle):
        bundle = seal(bundle)
        atomic(self.state / 'attempt.json', canonical(bundle))
        pending = json.loads(self.pending.read_bytes())
        pending.update(phase='deploy', attempt_checksum=bundle['checksum'])
        atomic(self.pending, canonical(pending))

    def preflight(self):
        self.stage = "preflight"
        host = os.environ.get('DOCKER_HOST', '')
        require(not host or host.startswith('unix://'), 'Only a local Unix-socket Docker daemon is supported', 64)
        endpoint = self.docker('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}').strip()
        require(endpoint.startswith('unix://'), 'Remote Docker contexts are not supported', 64)
        self.docker("info")
        self.docker("compose", "version")
        root = self.docker("info", "--format", "{{.DockerRootDir}}").strip()
        for path in (self.state, Path(self.cfg["BACKUP_DIR"]), Path(root)):
            while not path.exists():
                path = path.parent
            require(shutil.disk_usage(path).free // (1024 * 1024) >= int(self.cfg["MIN_FREE_MB"]), "Insufficient free disk space (MIN_FREE_MB)")

    def release_path(self, target):
        require(ID.fullmatch(target or ""), "An explicit valid --target release ID is required", 64)
        return self.state / "releases" / target / "release.json"

    def load(self, path=None):
        return validate_bundle(json.loads((path or self.current).read_bytes()))

    def pull(self, c, names=()):
        self.stage = "pull"
        self.dc(c, "pull", *names)

    def pin(self, c, names=SERVICES):
        for name in names:
            ref = c["services"][name]["image"]
            repo = REGISTRY + IMAGE_REPOS.get(name, name)
            digests = json.loads(self.docker("image", "inspect", "--format", "{{json .RepoDigests}}", ref))
            matches = [d for d in (digests or []) if d.startswith(repo + "@sha256:")]
            require(matches, "Pulled image has no immutable repository digest: " + name)
            c["services"][name]["image"] = matches[0]

    def check_compatibility(self, bundle):
        self.stage = "compatibility"
        c = bundle["compose"]
        validate_compose(c, pinned=True, allow_local=bool(bundle.get("restored_from")))
        version = self.docker("run", "--rm", "--pull=never", "--network", "none", "--entrypoint", "gvmd",
                              c["services"]["gvmd"]["image"], "--version")
        match = re.search(r"(?:Greenbone Vulnerability Manager|gvmd)\s+(\d+)\.", version, re.I)
        require(match and int(match[1]) in (25, 26), "Unknown gvmd/feed compatibility; review official release notes before changing policy")
        require(int(match[1]) == bundle["gvmd_major"], "Pinned manager does not match its release metadata")

    def prepare(self, args):
        path = self.release_path(args.target)
        require(not path.exists(), "Release ID already exists; choose a new ID", 64)
        require(args.approve_set and args.evidence and args.evidence.startswith(("https://greenbone.github.io/",
                "https://github.com/greenbone/", "https://forum.greenbone.net/")),
                "prepare requires --approve-set and an official --evidence URL for the reviewed component set", 64)
        self.stage = "prepare"
        source = Path(args.compose).resolve(strict=True)
        rendered = self.docker("compose", "--env-file", "/dev/null", "-p", self.cfg["PROJECT"], "-f", str(source), "config", "--format", "json")
        c = json.loads(rendered)
        validate_compose(c, pinned=False)
        references = {s: v["image"] for s, v in c["services"].items()}
        self.pull(c)
        self.pin(c)
        bundle = {"schema": 1, "id": args.target, "compose": c, "references": references,
                  "feed_release": "24.10", "gvmd_major": args.gvmd_major, "evidence": args.evidence}
        self.check_compatibility(bundle)
        atomic(path, canonical(seal(bundle)))
        atomic(path.parent / 'source.yaml', source.read_bytes())
        self.log("Prepared immutable release " + args.target + "; deployment requires a separate command")

    def window(self, explicit):
        if explicit:
            return
        window = self.cfg["AUTO_WINDOW_UTC"]
        require(window and self.cfg["AUTO_WINDOW_ACK"] == "1", "A maintenance window is required; block external scan admission and detach task schedules", 76)
        start, end = window.split("-")
        now = datetime.now(timezone.utc).strftime("%H:%M")
        inside = start <= now < end if start < end else now >= start or now < end
        require(start != end and inside, "Outside configured automatic maintenance window (UTC)", 76)

    def probe(self, c, mode):
        private_file(self.cfg["GMP_CREDENTIALS_FILE"])
        credentials = json.loads(Path(self.cfg["GMP_CREDENTIALS_FILE"]).read_bytes())
        c = copy.deepcopy(c)
        if mode == 'health':
            credentials['database'] = self.database_status(c)
            for source, directory in (('vt_data_vol', 'vt'), ('scap_data_vol', 'scap'),
                                      ('cert_data_vol', 'cert'), ('data_objects_vol', 'objects')):
                c['services']['gvm-tools']['volumes'].append({'type': 'volume', 'source': source,
                    'target': '/maintenance-feeds/' + directory, 'read_only': True})
        script = Path(__file__).with_name("gmp_probe.py").resolve()
        return self.dc(c, "run", "--rm", "--no-deps", "--pull", "never", "-T", "--volume",
                       str(script) + ":/maintenance-probe.py:ro", "--entrypoint", "python3", "gvm-tools",
                       "/maintenance-probe.py", mode, input=canonical(credentials), timeout=90)

    def database_status(self, c):
        self.stage = 'feed-database'
        schema = self.dc(c, 'exec', '-T', '-u', 'postgres', 'pg-gvm', 'psql', '-X', '-A', '-t',
                         '-v', 'ON_ERROR_STOP=1', '-d', 'gvmd', '-c', "SELECT to_regclass('vts.meta') IS NOT NULL").strip()
        require(schema in ('t', 'f'), 'Cannot determine feed database schema', 73)
        nvt_schema = 'vts' if schema == 't' else 'public'
        query = "SELECT json_build_object('nvt',(SELECT value FROM " + nvt_schema + ".meta WHERE name='nvts_feed_version')," \
            "'scap',(SELECT value FROM scap.meta WHERE name='last_update')," \
            "'cert',(SELECT value FROM cert.meta WHERE name='last_update')," \
            "'config',(SELECT coalesce(json_object_agg(uuid,modification_time),'{}'::json) FROM public.configs)," \
            "'port_list',(SELECT coalesce(json_object_agg(uuid,modification_time),'{}'::json) FROM public.port_lists)," \
            "'report_format',(SELECT coalesce(json_object_agg(uuid,modification_time),'{}'::json) FROM public.report_formats))"
        raw = self.dc(c, 'exec', '-T', '-u', 'postgres', 'pg-gvm', 'psql', '-X', '-A', '-t',
                      '-v', 'ON_ERROR_STOP=1', '-d', 'gvmd', '-c', query, timeout=60)
        return json.loads(raw)

    def scan_gate(self, c):
        self.stage = "scan-gate"
        try:
            reply = json.loads(self.probe(c, "idle"))
        except (Failure, ValueError):
            raise Failure("Scan state unavailable; no data changes permitted", 76) from None
        require(reply == {"idle": True}, "Active/queued scans or assigned schedules block maintenance", 76)

    def states(self, c):
        ids = self.dc(c, "ps", "-a", "-q").split()
        require(ids, "No deployed containers found", 73)
        containers = json.loads(self.docker("inspect", *ids))
        validate_mounts(c, containers)
        references = sorted({s['image'] for s in c['services'].values()})
        images = json.loads(self.docker('image', 'inspect', *references))
        require(len(images) == len(references), 'Incomplete local image inventory', 73)
        identities = dict(zip(references, (i['Id'] for i in images)))
        states = {}
        for container in containers:
            labels = container.get("Config", {}).get("Labels", {})
            name = labels.get("com.docker.compose.service")
            if labels.get("com.docker.compose.oneoff", "False").lower() != "true":
                require(name in c['services'] and container['Image'] == identities[c['services'][name]['image']],
                        'Running image differs from the accepted release: ' + str(name), 73)
                require(name not in states, "Unexpected scaled/duplicate service", 73)
                states[name] = container["State"]
        return states

    def health_once(self, c):
        validate_states(c, self.states(c))
        require(json.loads(self.probe(c, "health")) == {"healthy": True}, "Manager/scanner/feed functional check failed", 73)

    def health(self, c):
        self.stage = "health"
        deadline = time.monotonic() + int(self.cfg["HEALTH_TIMEOUT"])
        last = "Services are not ready"
        while time.monotonic() < deadline:
            try:
                self.health_once(c)
                self.log("Manager, scanner, feeds and container lifecycles are ready")
                return
            except (Failure, ValueError) as error:
                last = str(error) if isinstance(error, Failure) else "Invalid probe response"
                time.sleep(5)
        raise Failure("Health timeout: " + last, 73)

    def stop(self, c):
        self.stage = "stop"
        self.dc(c, "stop", "--timeout", "120", timeout=300)
        for name, state in self.states(c).items():
            require(state.get("Status") == "exited" and state.get("ExitCode") != 137, "Container did not stop cleanly: " + str(name))
        for volume in c["volumes"].values():
            require(not self.docker("ps", "-q", "--filter", "volume=" + volume["name"]).strip(), "Another running container uses a stack volume")

    def start(self, c):
        self.stage = "deploy"
        self.dc(c, "up", "-d", "--pull", "never", *[s for s in SERVICES if s != "gvm-tools"])
        self.health(c)

    def protect_images(self, bundle):
        self.stage = "protect-images"
        tags = {}
        for image in sorted({s["image"] for s in bundle["compose"]["services"].values()}):
            digest = image.split("@")[-1].removeprefix("sha256:")
            tag = "greenbone-maintenance/" + self.cfg["PROJECT"] + ":" + digest
            self.docker("image", "tag", image, tag)
            tags[image] = tag
        return tags

    def volume_export(self, image, volume, path):
        with Path(path).open("wb") as archive:
            self.docker("run", "--rm", "--pull=never", "--network", "none", "--user", "0", "--entrypoint", "tar",
                        "--mount", "type=volume,src=" + volume + ",dst=/source,readonly", image, "-C", "/source", "-cpf", "-", ".", stdout=archive)

    def volume_import(self, image, volume, path):
        with Path(path).open("rb") as archive:
            try:
                self.docker('run', '--rm', '-i', '--pull=never', '--network', 'none', '--user', '0',
                            '--entrypoint', 'tar', '--mount', 'type=volume,src=' + volume + ',dst=/dest',
                            image, '-C', '/dest', '-xpf', '-', stdin=archive)
            except Failure:
                raise Failure("Volume restoration failed or timed out", 74) from None

    def snapshot(self, bundle):
        self.stage = "backup"
        parent = Path(self.cfg["BACKUP_DIR"])
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = "backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        partial = parent / ("." + identity + ".partial")
        partial.mkdir(mode=0o700)
        (partial / "volumes").mkdir(mode=0o700)
        atomic(partial / "release.json", canonical(bundle))
        shutil.copyfile(self.cfg["CONFIG_FILE"], partial / "config.env")
        private_file(self.cfg["GMP_CREDENTIALS_FILE"])
        shutil.copyfile(self.cfg["GMP_CREDENTIALS_FILE"], partial / "credentials.json")
        tags = self.protect_images(bundle)
        image_fingerprints = {tag: self.image_fingerprint(tag) for tag in tags.values()}
        self.docker("image", "save", "-o", str(partial / "images.tar"), *sorted(tags.values()))
        image = bundle["compose"]["services"]["pg-gvm"]["image"]
        volumes = {}
        for key, volume in bundle["compose"]["volumes"].items():
            if key in SOCKETS:
                continue
            self.docker("volume", "inspect", volume["name"])
            self.volume_export(image, volume["name"], partial / "volumes" / (key + ".tar"))
            volumes[key] = volume["name"]
        atomic(partial / "backup.json", canonical({"schema": 1, "kind": "cold-all-volumes",
               "project": self.cfg["PROJECT"], "volumes": volumes, "release": bundle["id"],
               "image_tags": tags, "image_fingerprints": image_fingerprints,
               "created": datetime.now(timezone.utc).isoformat()}))
        files = sorted(p for p in partial.rglob("*") if p.is_file())
        for path in files:
            path.chmod(0o600)
            with path.open('rb') as handle:
                os.fsync(handle.fileno())
        atomic(partial / "checksums.json", canonical({str(p.relative_to(partial)): file_hash(p) for p in files}))
        self.verify_backup(partial)
        final = parent / identity
        os.replace(partial, final)
        self.log("Verified cold backup: " + str(final))
        self.copy_backup(final)
        return final

    def verify_backup(self, path):
        try:
            return self._verify_backup(path)
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
            raise Failure('Backup metadata or archive is missing, invalid or unreadable', 74) from None

    def _verify_backup(self, path):
        path = Path(path)
        require(path.is_dir() and not path.is_symlink(), "Backup directory missing or unsafe", 74)
        checksums = json.loads((path / "checksums.json").read_bytes())
        expected = {"release.json", "config.env", "credentials.json", "backup.json", "images.tar"}
        expected.update("volumes/" + v + ".tar" for v in VOLUMES if v not in SOCKETS)
        require(set(checksums) == expected, "Incomplete backup inventory", 74)
        actual = {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()}
        require(actual == expected | {"checksums.json"}, "Unexpected backup files", 74)
        for name, digest in checksums.items():
            file = path / name
            require(not file.is_symlink() and file_hash(file) == digest, "Backup checksum mismatch", 74)
        bundle = self.load(path / "release.json")
        metadata = json.loads((path / "backup.json").read_bytes())
        require(metadata.get("schema") == 1 and metadata.get("kind") == "cold-all-volumes"
                and metadata.get("project") == self.cfg["PROJECT"]
                and set(metadata.get("volumes", {})) == set(VOLUMES) - SOCKETS,
                "Backup metadata does not match this project/layout", 74)
        images = {s['image'] for s in bundle['compose']['services'].values()}
        tags = metadata.get('image_tags', {})
        fingerprints = metadata.get('image_fingerprints', {})
        require(set(tags) == images and set(tags.values()) == set(fingerprints)
                and all(re.fullmatch(r'greenbone-maintenance/[a-z0-9_.-]+:[a-f0-9]{64}', tag) for tag in tags.values())
                and all(re.fullmatch(r'[a-f0-9]{64}', value) for value in fingerprints.values()),
                'Incomplete backup image identity inventory', 74)
        with tarfile.open(path / 'images.tar', 'r') as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
            archived_tags = {tag for image in manifest for tag in (image.get('RepoTags') or [])}
            require(set(tags.values()).issubset(archived_tags), 'Required images missing from archive', 74)
            for image in manifest:
                for name in [image['Config'], *image['Layers']]:
                    require(archive.getmember(name).isfile(), 'Incomplete Docker image archive', 74)
        for name in metadata["volumes"]:
            with tarfile.open(path / "volumes" / (name + ".tar"), "r|") as archive:
                for member in archive:
                    p = PurePosixPath(member.name)
                    require(not p.is_absolute() and ".." not in p.parts and not member.isdev(), "Unsafe volume archive entry", 74)
                    if member.issym() or member.islnk():
                        link = PurePosixPath(member.linkname)
                        require(not link.is_absolute() and ".." not in link.parts, "External archive links require manual restoration", 74)
        require((path / "images.tar").stat().st_size > 0, "Empty image archive", 74)
        return bundle

    def copy_backup(self, backup):
        destination = self.cfg["BACKUP_COPY_DIR"]
        if not destination:
            self.log("No separate backup destination configured; local backup only")
            return
        destination = Path(destination).resolve()
        require(destination.is_dir() and destination != backup.parent.resolve() and backup.parent.resolve() not in destination.parents,
                "BACKUP_COPY_DIR must be an existing separate destination", 74)
        partial = destination / ("." + backup.name + ".partial")
        shutil.copytree(backup, partial)
        self.verify_backup(partial)
        os.replace(partial, destination / backup.name)
        self.log("Verified backup copy at separate destination")

    def retention(self, protect):
        parent = Path(self.cfg["BACKUP_DIR"])
        paths = sorted(parent.glob("backup-*"), reverse=True)
        keep = set(paths[:int(self.cfg["KEEP_BACKUPS"])] + [Path(protect)])
        anchor = self.state / "last-upgrade-backup"
        if anchor.exists():
            keep.add(Path(anchor.read_text().strip()))
        for path in paths:
            if path not in keep and path.is_dir() and not path.is_symlink():
                self.verify_backup(path)
                shutil.rmtree(path)

    def finish(self, bundle):
        atomic(self.current, canonical(seal(bundle)))
        self.pending.unlink()
        self.log("Operation completed and verified")

    def backup(self, explicit):
        bundle = self.load()
        self.window(explicit)
        self.scan_gate(bundle["compose"])
        self.health_once(bundle["compose"])
        self.scan_gate(bundle["compose"])
        self.write_pending("backup", bundle["id"])
        self.stop(bundle["compose"])
        path = self.snapshot(bundle)
        self.start(bundle["compose"])
        self.finish(bundle)
        self.retention(path)

    def feed_update(self, explicit):
        bundle = self.load()
        self.window(explicit)
        self.check_compatibility(bundle)
        self.scan_gate(bundle["compose"])
        candidate = copy.deepcopy(bundle)
        c = candidate["compose"]
        for name in FEEDS:
            c["services"][name]["image"] = TRACKING_REFERENCES[name]
            candidate['references'][name] = TRACKING_REFERENCES[name]
        self.pull(c, FEEDS)
        self.pin(c, FEEDS)
        if 'repository_digests' in candidate:
            for name in FEEDS:
                candidate['repository_digests'][name] = c['services'][name]['image']
        validate_compose(c, pinned=True, allow_local=bool(candidate.get("restored_from")))
        self.scan_gate(bundle["compose"])
        self.write_pending("feed-update", bundle["id"])
        self.stop(bundle["compose"])
        self.record_attempt(candidate)
        self.start(c)
        self.protect_images(candidate)
        self.finish(candidate)

    def upgrade(self, target, explicit, bootstrap=False):
        self.window(explicit)
        candidate = self.load(self.release_path(target))
        self.check_compatibility(candidate)
        old = None if bootstrap else self.load()
        if old:
            preserve_volumes(old["compose"], candidate["compose"])
            candidate = seal(candidate)
        if bootstrap:
            require(not self.current.exists(), "Stack already managed; use upgrade", 64)
            require(not self.docker("ps", "-aq", "--filter", "label=com.docker.compose.project=" + self.cfg["PROJECT"]).strip(),
                    "Existing project requires the adoption procedure")
            existing = set(self.docker("volume", "ls", "-q").split())
            require(not existing.intersection(v["name"] for v in candidate["compose"]["volumes"].values()), "Existing volumes require adoption")
        else:
            self.scan_gate(old["compose"])
            self.health_once(old["compose"])
        # Pull immutable digests before downtime. A failed pull leaves the stack unchanged.
        self.pull(candidate["compose"])
        self.protect_images(candidate)
        if old:
            self.scan_gate(old["compose"])
        self.write_pending("bootstrap" if bootstrap else "upgrade", target)
        if old:
            self.stop(old["compose"])
            backup = self.snapshot(old)
            atomic(self.state / "last-upgrade-backup", (str(backup) + "\n").encode())
            self.write_pending("upgrade", target, backup)
        self.record_attempt(candidate)
        self.start(candidate["compose"])
        self.finish(candidate)

    def restore(self, path, confirmation, explicit):
        require(confirmation == self.cfg["PROJECT"], "Restore requires --confirm-project with the exact PROJECT", 64)
        self.window(explicit)
        self.stage = "restore-verify"
        path = Path(path).resolve(strict=True)
        saved = self.verify_backup(path)
        saved.setdefault('repository_digests', {s: v['image'] for s, v in saved['compose']['services'].items()})
        self.docker("image", "load", "-i", str(path / "images.tar"))
        metadata = json.loads((path / "backup.json").read_bytes())
        for image, tag in metadata["image_tags"].items():
            require(self.image_fingerprint(tag) == metadata["image_fingerprints"][tag], "Loaded image differs from backup", 74)
            local_id = self.docker("image", "inspect", "--format", "{{.Id}}", tag).strip()
            require(DIGEST.fullmatch(local_id), "Loaded image has no immutable local ID", 74)
            for service in saved["compose"]["services"].values():
                if service["image"] == image:
                    service["image"] = local_id
        saved["restored_from"] = str(path)
        saved = seal(saved)
        self.check_compatibility(saved)
        live = self.load() if self.current.exists() else saved
        running = self.docker("ps", "-q", "--filter", "label=com.docker.compose.project=" + self.cfg["PROJECT"]).strip()
        if running:
            self.scan_gate(live["compose"])
        self.write_pending("restore", saved["id"], path)
        self.stage = "restore"
        # down never removes volumes; old data survives even a failed restore.
        self.dc(live["compose"], "down", "--timeout", "120", timeout=300)
        restored = copy.deepcopy(saved)
        suffix = uuid.uuid4().hex[:12]
        helper = saved["compose"]["services"]["pg-gvm"]["image"]
        for key in VOLUMES:
            volume = self.cfg["PROJECT"] + "_restore_" + suffix + "_" + key
            self.docker("volume", "create", volume)
            restored["compose"]["volumes"][key] = {"name": volume}
            if key not in SOCKETS:
                self.volume_import(helper, volume, path / "volumes" / (key + ".tar"))
        # The saved database and saved probe identity must agree.
        atomic(Path(self.cfg["GMP_CREDENTIALS_FILE"]), (path / "credentials.json").read_bytes())
        restored = seal(restored)
        self.record_attempt(restored)
        self.start(restored["compose"])
        self.protect_images(restored)
        self.finish(restored)
        self.log("Restored backup into new volumes; previous volumes retained")

    def resume(self, explicit):
        self.window(explicit)
        require(self.pending.exists(), 'No incomplete operation to resume', 64)
        pending = json.loads(self.pending.read_bytes())
        if pending.get('phase') == 'pre-data':
            require(self.current.exists(), 'No running baseline; inspect bootstrap state manually', 70)
            bundle = self.load()
        else:
            bundle = self.load(self.state / 'attempt.json')
            require(bundle['checksum'] == pending.get('attempt_checksum'), 'Recovery attempt identity mismatch', 70)
        if self.docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + self.cfg['PROJECT']).strip():
            self.scan_gate(bundle['compose'])
        self.log('Operator requested resume of the recorded image/data set; no data restoration')
        self.start(bundle['compose'])
        self.finish(bundle)

    def image_fingerprint(self, image):
        info = json.loads(self.docker("image", "inspect", image))[0]
        identity = {key: info[key] for key in ("Architecture", "Os", "Config", "RootFS")}
        return hashlib.sha256(canonical(identity)).hexdigest()

    def adopt(self, target, explicit):
        self.window(explicit)
        require(not self.current.exists(), "Project already managed", 64)
        bundle = self.load(self.release_path(target))
        self.check_compatibility(bundle)
        self.scan_gate(bundle["compose"])
        self.health_once(bundle["compose"])
        ids = self.dc(bundle["compose"], "ps", "-a", "-q").split()
        for container in json.loads(self.docker("inspect", *ids)):
            name = container["Config"]["Labels"].get("com.docker.compose.service")
            if name in SERVICES and name != "gvm-tools":
                expected = self.docker("image", "inspect", "--format", "{{.Id}}", bundle["compose"]["services"][name]["image"]).strip()
                require(container["Image"] == expected, "Prepared release does not match running image: " + name)
        self.protect_images(bundle)
        atomic(self.current, canonical(bundle))
        self.log("Adopted matching running images; take a cold backup before any upgrade")

    def version_check(self):
        bundle = self.load()
        self.stage = "version-check"
        with urlopen(self.cfg["COMPOSE_URL"], timeout=30) as response:
            upstream = response.read(1024 * 1024)
        previous = self.state / "releases" / bundle["id"] / "source.sha256"
        current_hash = hashlib.sha256(upstream).hexdigest()
        if previous.exists():
            self.log("Upstream Compose " + ("unchanged" if previous.read_text().strip() == current_hash else "changed; operator review required"))
        else:
            self.log("Upstream Compose SHA256=" + current_hash + "; compare against the reviewed source")
        for name in SERVICES:
            ref = TRACKING_REFERENCES[name]
            raw = self.docker("buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}")
            remote = json.loads(raw).get("digest")
            require(remote and DIGEST.fullmatch(remote), "Registry digest unavailable; version status unknown")
            local = bundle.get('repository_digests', {}).get(name, bundle['compose']['services'][name]['image']).split('@')[-1]
            self.log(name + (": remote image differs (report only)" if remote != local else ": unchanged"))
        self.log("Version report complete; no deployment performed")

    def execute(self, args):
        if self.dry_run:
            self.stage = "dry-run"
            if args.command in ("upgrade", "bootstrap", "adopt"):
                self.load(self.release_path(args.target))
            elif self.current.exists():
                self.load()
            self.log("Configuration/local bundle validation only; no Docker, locks, logs or scheduler changes")
            return
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock():
            if args.command == "verify-backup":
                self.verify_backup(Path(args.backup))
                self.log("Backup integrity verified")
                return
            if args.command == "status":
                self.log("Current release: " + self.load()["id"] if self.current.exists() else "No current managed release")
                if self.pending.exists():
                    pending = json.loads(self.pending.read_bytes())
                    self.log("INCOMPLETE " + pending.get("operation", "unknown") + "; backup=" + pending.get("backup", ""))
                    raise Failure("Manual recovery required", 70)
                return
            if args.command not in ("restore", "resume", "doctor", "version-check"):
                self.require_clean()
            self.preflight()
            if args.command == "prepare":
                self.prepare(args)
                source_hash = hashlib.sha256(Path(args.compose).read_bytes()).hexdigest()
                atomic(self.release_path(args.target).parent / "source.sha256", (source_hash + "\n").encode())
            elif args.command == "doctor":
                self.health_once(self.load()["compose"])
                self.log("Doctor passed; capacity and a full scan still require a pilot")
                self.require_clean()
            elif args.command == "version-check":
                self.version_check()
            elif args.command == "feed-update":
                self.feed_update(args.maintenance_window)
            elif args.command == "backup":
                self.backup(args.maintenance_window)
            elif args.command in ("upgrade", "bootstrap"):
                self.upgrade(args.target, args.maintenance_window, args.command == "bootstrap")
            elif args.command == "restore":
                self.restore(args.backup, args.confirm_project, args.maintenance_window)
            elif args.command == 'resume':
                self.resume(args.maintenance_window)
            elif args.command == "adopt":
                self.adopt(args.target, args.maintenance_window)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Controlled Greenbone maintenance; no implicit update")
    parser.add_argument("--config", default=os.environ.get("GB_CONFIG_FILE", "@CONFIG_FILE@"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "bootstrap", "upgrade", "adopt", "status", "doctor", "feed-update", "version-check", "backup", "verify-backup", "restore", "resume"):
        sub = commands.add_parser(name)
        sub.add_argument("--dry-run", action="store_true", default=os.environ.get("GB_DRY_RUN") == "1")
        if name in ("bootstrap", "upgrade", "adopt", "prepare"):
            sub.add_argument("--target", required=True, help="Operator-named reviewed release snapshot")
        if name == "prepare":
            sub.add_argument("--compose", required=True, help="Local reviewed Compose source")
            sub.add_argument("--evidence", required=True, help="Official release/source reference")
            sub.add_argument("--approve-set", action="store_true", help="Affirm the component set was reviewed together")
            sub.add_argument("--gvmd-major", type=int, choices=(25, 26), required=True)
        if name in ("bootstrap", "upgrade", "adopt", "feed-update", "backup", "restore", "resume"):
            sub.add_argument("--maintenance-window", action="store_true", help="Affirm external scan admission is blocked and task schedules detached")
        if name in ("restore", "verify-backup"):
            sub.add_argument("--backup", required=True)
        if name == "restore":
            sub.add_argument("--confirm-project", required=True)
    return parser.parse_args(argv)


def main():
    os.umask(0o077)
    manager = None
    try:
        args = arguments()
        manager = Manager(read_config(args.config), args.dry_run)
        manager.execute(args)
        return 0
    except Failure as error:
        print("ERROR [" + (manager.stage if manager else "config") + "]: " + str(error), file=sys.stderr)
        if manager and manager.pending.exists():
            print("Recovery marker retained. No data was automatically restored. Use status and the restore runbook.", file=sys.stderr)
        return error.code
    except (OSError, ValueError, KeyError, tarfile.TarError):
        # Exception text may embed secret values; never print it.
        print("ERROR: Invalid or inaccessible configuration, state, backup or daemon response", file=sys.stderr)
        return 74 if manager and manager.pending.exists() else 64
    except KeyboardInterrupt:
        print("ERROR: Interrupted; inspect status before retrying", file=sys.stderr)
        return 70


if __name__ == "__main__":
    sys.exit(main())
