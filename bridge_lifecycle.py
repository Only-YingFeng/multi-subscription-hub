"""Manage one private, loopback-only compatible core; never edit Clash profiles."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

import yaml


KIND = "codex-feiniao-bridge"
SCHEMA = 1
EXPECTED_COUNT = 108
STAGE_CONFIG = "飞鸟桥接-108节点.private.yaml"
STAGE_IMPORT = "飞鸟加速-108节点-Clash桥接导入.yaml"
STAGE_MAP = "bridge-port-map.private.json"
STAGE_CORE = "backup/core-binaries/feiniao-compatible.exe"
STAGE_PREFLIGHT = "prepared-108-preflight.json"
OUTPUTS = ("config.yaml", "import.yaml", "port-map.json", "core.exe")
ROOT_ITEMS = set(OUTPUTS) | {
    "manifest.json", "source-hashes.json", "state.json", "data", "inputs", "logs",
    "start.ps1", "stop.ps1", "status.ps1", ".operation.lock",
}


class BridgeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise BridgeError(code)


def _is_reparse(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def safe_child(root, relative, *, must_exist=False):
    """Refuse traversal, NTFS streams and junctions, including in existing parents."""
    root = Path(root).absolute()
    relative = Path(relative)
    require(not relative.is_absolute() and bool(relative.parts), "UNSAFE_PATH")
    require(all(part not in (".", "..") and ":" not in part for part in relative.parts), "UNSAFE_PATH")
    child = root / relative
    for path in (root, *root.parents):
        if path.exists() or path.is_symlink():
            require(not _is_reparse(path), "UNSAFE_PATH")
    path = root
    for part in relative.parts:
        path /= part
        if path.exists() or path.is_symlink():
            require(not _is_reparse(path), "UNSAFE_PATH")
    require(child.resolve().is_relative_to(root.resolve()), "UNSAFE_PATH")
    if must_exist:
        require(child.is_file(), "INPUT_MISSING")
    return child


def _read(path, limit=16 * 1024 * 1024):
    require(path.is_file() and path.stat().st_size <= limit, "INPUT_SIZE_OR_TYPE")
    return path.read_bytes()


def read_json(path):
    try:
        return json.loads(_read(path, 2 * 1024 * 1024))
    except (ValueError, UnicodeError):
        raise BridgeError("INVALID_JSON") from None


def read_yaml(path):
    try:
        value = yaml.safe_load(_read(path).decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeError):
        raise BridgeError("INVALID_YAML") from None
    require(isinstance(value, dict), "INVALID_CONFIG")
    return value


class QuotedDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


QuotedDumper.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style='"'))


def yaml_bytes(value):
    return yaml.dump(value, Dumper=QuotedDumper, allow_unicode=True, sort_keys=False).encode("utf-8")


@contextmanager
def _child_dll_search():
    if not (os.name == "nt" and getattr(sys, "frozen", False)):
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    original = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(original), original)
    require(kernel.SetDllDirectoryW(None), "CHILD_DLL_SEARCH_FAILED")
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(original.value or None)


def atomic_json(path, value):
    path = path.parent.resolve() / path.name
    temporary = safe_child(path.parent, path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_bundle(config, imported, mapping, *, expected_count=EXPECTED_COUNT):
    nodes = config.get("proxies")
    entries = mapping.get("entries") if isinstance(mapping, dict) else None
    require(isinstance(nodes, list) and len(nodes) == expected_count, "NODE_COUNT")
    require(isinstance(entries, list) and len(entries) == expected_count and mapping.get("version") == 1, "MAP_SCHEMA")
    names = set()
    for node in nodes:
        require(isinstance(node, dict), "NODE_SCHEMA")
        name = node.get("name")
        require(isinstance(name, str) and name.strip() and not any(c in name for c in ",\r\n"), "NODE_NAME")
        require(name not in names and name not in ("DIRECT", "REJECT"), "DUPLICATE_NODE_NAME")
        names.add(name)
        require(node.get("type") in ("vless", "anytls"), "NODE_PROTOCOL")
        require(isinstance(node.get("server"), str) and node["server"], "NODE_SERVER")
        require(type(node.get("port")) is int and 1 <= node["port"] <= 65535, "NODE_PORT")
        require("uid" in node, "VENDOR_AUTH_MISSING")
        auth = "uuid" if node["type"] == "vless" else "password"
        require(isinstance(node.get(auth), str) and node[auth], "NODE_AUTH")
        if node["type"] == "vless":
            require(node.get("tls") is True and "version" in node, "VENDOR_VLESS_FIELDS")
        require(not node.get("dialer-proxy"), "OUTBOUND_CHAIN_FORBIDDEN")
    by_name, ports, identities = {}, set(), set()
    for entry in entries:
        require(isinstance(entry, dict), "MAP_SCHEMA")
        name, port, identity = entry.get("nodeName"), entry.get("port"), entry.get("identity")
        require(name in names and name not in by_name, "MAP_NODE_MISMATCH")
        require(type(port) is int and 1024 <= port <= 65535 and port not in ports, "MAP_PORT")
        require(isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity) and identity not in identities, "MAP_IDENTITY")
        require(not entry.get("removed") and not entry.get("tombstone"), "MAP_ACTIVE_SCHEMA")
        by_name[name] = port
        ports.add(port)
        identities.add(identity)
    require(config.get("allow-lan") is False and config.get("bind-address") == "127.0.0.1", "PUBLIC_BIND")
    require(config.get("mode") == "rule", "BRIDGE_MODE")
    require(isinstance(config.get("tun"), dict) and config["tun"].get("enable") is False, "TUN_FORBIDDEN")
    dns = config.get("dns", {})
    require(isinstance(dns, dict) and not dns.get("listen"), "DNS_LISTENER_FORBIDDEN")
    for key in ("external-controller", "external-controller-tls", "external-controller-unix", "external-controller-pipe", "port", "socks-port", "mixed-port", "redir-port", "tproxy-port"):
        require(not config.get(key), "EXTRA_LISTENER_FORBIDDEN")
    require(not config.get("proxy-providers") and not config.get("proxy-groups") and not config.get("sub-rules"), "EXTRA_ROUTING_FORBIDDEN")
    listeners = config.get("listeners")
    require(isinstance(listeners, list) and len(listeners) == expected_count, "LISTENER_COUNT")
    listener_ports, listener_names = set(), set()
    for listener in listeners:
        require(isinstance(listener, dict) and set(listener) <= {"name", "type", "listen", "port", "udp"}, "LISTENER_SCHEMA")
        require(listener.get("type") == "socks" and listener.get("listen") == "127.0.0.1" and listener.get("udp") is False, "LISTENER_NOT_PRIVATE_TCP")
        port, name = listener.get("port"), listener.get("name")
        require(type(port) is int and port in ports and port not in listener_ports, "LISTENER_PORT")
        require(isinstance(name, str) and name and name not in listener_names, "LISTENER_NAME")
        listener_ports.add(port)
        listener_names.add(name)
    rules = config.get("rules")
    expected_rules = {f"IN-PORT,{port},{name}" for name, port in by_name.items()}
    require(isinstance(rules, list) and len(rules) == expected_count + 1 and rules[-1] == "MATCH,REJECT", "DEFAULT_REJECT_REQUIRED")
    require(set(rules[:-1]) == expected_rules, "IN_PORT_BINDING")
    local_nodes = imported.get("proxies")
    require(isinstance(local_nodes, list) and len(local_nodes) == expected_count, "IMPORT_NODE_COUNT")
    import_names = set()
    for node in local_nodes:
        require(isinstance(node, dict) and set(node) == {"name", "type", "server", "port", "udp"}, "IMPORT_NODE_SCHEMA")
        name = node["name"]
        require(name in by_name and name not in import_names, "IMPORT_NODE_NAME")
        require(node["type"] == "socks5" and node["server"] == "127.0.0.1" and type(node["port"]) is int and node["port"] == by_name[name] and node["udp"] is False, "IMPORT_NOT_FIXED_LOOPBACK")
        import_names.add(name)
    require(set(imported) == {"proxies", "proxy-groups", "rules"}, "IMPORT_EXTRA_SETTINGS")
    require(isinstance(imported["proxy-groups"], list) and isinstance(imported["rules"], list), "IMPORT_TOPOLOGY")
    return sorted(ports)


def _powershell(script, payload):
    require(os.name == "nt", "WINDOWS_REQUIRED")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    child_env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    child_env["PSModulePath"] = str(executable.parent / "Modules")
    with _child_dll_search():
        process = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); $p=[Console]::In.ReadToEnd() | ConvertFrom-Json; " + script], input=json.dumps(payload).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW, env=child_env)
    require(process.returncode == 0, "WINDOWS_QUERY_FAILED")
    try:
        return json.loads(process.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise BridgeError("WINDOWS_QUERY_FAILED") from None


def protect_directory(root):
    script = """
    $me=[Security.Principal.WindowsIdentity]::GetCurrent().User;
    $acl=New-Object Security.AccessControl.DirectorySecurity;
    $acl.SetAccessRuleProtection($true,$false); $acl.SetOwner($me);
    foreach($sid in @($me,(New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))) {
      $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow');
      $acl.AddAccessRule($rule);
    }
    Set-Acl -LiteralPath $p.path -AclObject $acl; 'true'
    """
    require(_powershell(script, {"path": str(root)}) is True, "ACL_FAILED")


def verify_acl(root):
    script = """
    $acl=Get-Acl -LiteralPath $p.path;
    $me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]));
    $allowed=@($me,'S-1-5-18');
    $ok=$acl.AreAccessRulesProtected -and ($rules.Count -eq 2);
    foreach($r in $rules) { $ok=$ok -and ($r.IdentityReference.Value -in $allowed) -and ($r.AccessControlType -eq 'Allow') -and (($r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl) }
    $ok=$ok -and (@($rules.IdentityReference.Value | Select-Object -Unique).Count -eq 2);
    ConvertTo-Json -InputObject ([bool]$ok)
    """
    require(_powershell(script, {"path": str(root)}) is True, "PRIVATE_ACL_REQUIRED")


@contextmanager
def operation_lock(root):
    path = safe_child(root, ".operation.lock")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise BridgeError("BUSY_OR_STALE_LOCK") from None
    try:
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def _load_owned(root, *, verify_files=True):
    manifest = read_json(safe_child(root, "manifest.json", must_exist=True))
    require(isinstance(manifest, dict) and manifest.get("kind") == KIND and manifest.get("schema") == SCHEMA and manifest.get("status") == "ready", "UNMANAGED_OR_INCOMPLETE_DIRECTORY")
    require(re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("install_id", ""))), "MANIFEST_INVALID")
    require({p.name for p in root.iterdir()} <= ROOT_ITEMS, "UNMANAGED_CONTENT")
    verify_acl(root)
    if verify_files:
        hashes = manifest.get("files", {})
        require(set(hashes) == set(OUTPUTS), "MANIFEST_INVALID")
        for name in OUTPUTS:
            require(file_hash(safe_child(root, name, must_exist=True)) == hashes[name], "MANAGED_FILE_CHANGED")
        ports = validate_bundle(read_yaml(root / "config.yaml"), read_yaml(root / "import.yaml"), read_json(root / "port-map.json"))
        require(ports == manifest.get("ports"), "MANIFEST_INVALID")
    return manifest


def _validate_core(root):
    with _child_dll_search(), safe_child(root, "logs/validation.private.log").open("wb") as log:
        process = subprocess.run([str(root / "core.exe"), "-t", "-d", str(root / "data"), "-f", str(root / "config.yaml")], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(process.returncode == 0, "CORE_VALIDATION_FAILED")


def _shortcut(root, action):
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = quote(Path(sys.executable).resolve())
    if not getattr(sys, "frozen", False):
        command += " -B " + quote(Path(__file__).resolve())
    script = "$ErrorActionPreference='Stop'\r\n& " + command + " --root " + quote(root) + " " + action + "\r\nexit $LASTEXITCODE\r\n"
    safe_child(root, action + ".ps1").write_text(script, encoding="utf-8-sig")


def install(root, stage):
    root, stage = Path(root).absolute(), Path(stage).absolute()
    require(stage.is_dir() and root != stage and not root.is_relative_to(stage), "INSTALL_PATH")
    paths = {name: safe_child(stage, name, must_exist=True) for name in (STAGE_CONFIG, STAGE_IMPORT, STAGE_MAP, STAGE_CORE, STAGE_PREFLIGHT)}
    config, imported, mapping = read_yaml(paths[STAGE_CONFIG]), read_yaml(paths[STAGE_IMPORT]), read_json(paths[STAGE_MAP])
    ports = validate_bundle(config, imported, mapping)
    preflight = read_json(paths[STAGE_PREFLIGHT])
    require(isinstance(preflight, dict) and preflight.get("nodeCount") == EXPECTED_COUNT and preflight.get("bridgeValidationExitCode") == 0 and preflight.get("importValidationExitCode") == 0, "STAGE_NOT_PREVALIDATED")
    for key in ("uniquePorts", "rulesRetainedExactly", "originalGroupTopologyRetained", "onlyLocalAddressesInClashNodes", "bridgeTunDisabled", "bridgeDnsListenerAbsent", "bridgeControlApiAbsent", "bridgeDefaultReject", "bridgeStillPrivate"):
        require(preflight.get(key) is True, "STAGE_NOT_PREVALIDATED")
    hashes = {name: file_hash(path) for name, path in paths.items()}
    if root.exists():
        manifest = _load_owned(root)
        require(manifest.get("source_hashes") == hashes, "STAGE_CHANGED_UPDATE_NOT_IMPLEMENTED")
        return {"ok": True, "action": "install", "installed": True, "unchanged": True, "nodes": EXPECTED_COUNT}
    safe_child(root.parent, root.name)
    root.mkdir(parents=True, exist_ok=False)
    protect_directory(root)
    verify_acl(root)
    for name in ("data", "inputs", "logs"):
        safe_child(root, name).mkdir()
    with operation_lock(root):
        for source, destination in ((STAGE_CONFIG, "inputs/config.original.yaml"), (STAGE_IMPORT, "inputs/import.original.yaml"), (STAGE_MAP, "inputs/map.original.json"), (STAGE_PREFLIGHT, "inputs/preflight.original.json")):
            shutil.copyfile(paths[source], safe_child(root, destination))
            require(file_hash(root / destination) == hashes[source], "STAGE_CHANGED_DURING_INSTALL")
        shutil.copyfile(paths[STAGE_CORE], root / "core.exe")
        require(file_hash(root / "core.exe") == hashes[STAGE_CORE], "STAGE_CHANGED_DURING_INSTALL")
        data_directory = safe_child(stage, "validation-data")
        require(data_directory.is_dir(), "VALIDATION_DATA_MISSING")
        for source in data_directory.iterdir():
            if source.suffix.lower() in (".dat", ".mmdb"):
                checked = safe_child(stage, "validation-data/" + source.name, must_exist=True)
                shutil.copyfile(checked, safe_child(root, "data/" + source.name))
        (root / "config.yaml").write_bytes(yaml_bytes(config))
        (root / "import.yaml").write_bytes(yaml_bytes(imported))
        atomic_json(root / "port-map.json", mapping)
        require(read_yaml(root / "config.yaml") == config and read_yaml(root / "import.yaml") == imported and read_json(root / "port-map.json") == mapping, "SERIALIZATION_CHANGED_DEFINITIONS")
        require(read_yaml(root / "inputs/config.original.yaml") == config and read_yaml(root / "inputs/import.original.yaml") == imported and read_json(root / "inputs/map.original.json") == mapping and read_json(root / "inputs/preflight.original.json") == preflight, "STAGE_CHANGED_DURING_INSTALL")
        _validate_core(root)
        for action in ("start", "stop", "status"):
            _shortcut(root, action)
        atomic_json(root / "source-hashes.json", hashes)
        manifest = {"kind": KIND, "schema": SCHEMA, "status": "ready", "install_id": uuid.uuid4().hex, "source_hashes": hashes, "files": {name: file_hash(root / name) for name in OUTPUTS}, "ports": ports, "node_count": EXPECTED_COUNT}
        atomic_json(root / "manifest.json", manifest)
    return {"ok": True, "action": "install", "installed": True, "unchanged": False, "nodes": EXPECTED_COUNT, "core_validated": True}


def listener_rows(pid, ports):
    script = "$ports=@($p.ports); $rows=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {($_.LocalPort -in $ports) -or ($_.OwningProcess -eq $p.pid)} | ForEach-Object { @{address=[string]$_.LocalAddress; port=[int]$_.LocalPort; pid=[int]$_.OwningProcess} }); ConvertTo-Json -InputObject $rows -Compress"
    value = _powershell(script, {"pid": pid, "ports": ports})
    require(isinstance(value, list), "LISTENER_QUERY_FAILED")
    return value


def listeners_match(rows, pid, ports):
    return len(rows) == len(ports) and {r.get("port") for r in rows} == set(ports) and all(r.get("pid") == pid and r.get("address") == "127.0.0.1" for r in rows)


def identity_matches(state, actual, root, manifest):
    if not isinstance(state, dict) or not isinstance(actual, dict):
        return False
    try:
        expected_exe = safe_child(root, "core.exe", must_exist=True).resolve()
        expected_config = safe_child(root, "config.yaml", must_exist=True).resolve()
        expected_args = [str(expected_exe), "-d", str((root / "data").resolve()), "-f", str(expected_config)]
        return (
            type(state.get("pid")) is int and state["pid"] > 0 and state["pid"] == actual.get("pid")
            and type(actual.get("pid")) is int
            and isinstance(state.get("creation_time"), str) and state["creation_time"].isdigit() and int(state["creation_time"]) > 0
            and str(state.get("creation_time")) == str(actual.get("creation_time"))
            and state.get("install_id") == manifest.get("install_id")
            and Path(state.get("exe", "")).resolve() == expected_exe
            and Path(actual.get("exe", "")).resolve() == expected_exe
            and Path(state.get("config", "")).resolve() == expected_config
            and actual.get("argv") == expected_args
            and state.get("exe_sha256") == manifest["files"]["core.exe"] == file_hash(expected_exe)
            and state.get("config_sha256") == manifest["files"]["config.yaml"] == file_hash(expected_config)
        )
    except (BridgeError, OSError, KeyError, TypeError, ValueError):
        return False


class ProcessHandle:
    """Keep the Windows process handle throughout verification and termination."""
    def __init__(self, pid, *, terminate=False):
        require(os.name == "nt", "WINDOWS_REQUIRED")
        self.pid = pid
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.handle = self.kernel.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, pid)
        if not self.handle:
            raise BridgeError("PROCESS_NOT_FOUND" if ctypes.get_last_error() == 87 else "PROCESS_QUERY_FAILED")

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.kernel.CloseHandle(self.handle)

    def snapshot(self):
        times = [wintypes.FILETIME() for _ in range(4)]
        require(self.kernel.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times]), "PROCESS_QUERY_FAILED")
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        require(self.kernel.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)), "PROCESS_QUERY_FAILED")
        value = _powershell("$v=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$p.pid); if($null -eq $v){'null'}else{ConvertTo-Json -InputObject ([string]$v.CommandLine) -Compress}", {"pid": self.pid})
        argv = []
        if isinstance(value, str):
            shell = ctypes.WinDLL("shell32", use_last_error=True)
            shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
            self.kernel.LocalFree.argtypes = [wintypes.HLOCAL]
            count = ctypes.c_int()
            pointer = shell.CommandLineToArgvW(value, ctypes.byref(count))
            require(bool(pointer), "PROCESS_QUERY_FAILED")
            try:
                argv = [pointer[i] for i in range(count.value)]
            finally:
                self.kernel.LocalFree(ctypes.cast(pointer, wintypes.HLOCAL))
        return {"pid": self.pid, "creation_time": str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime), "exe": buffer.value, "argv": argv}

    def stop(self):
        if self.kernel.WaitForSingleObject(self.handle, 0) != 0:
            require(self.kernel.TerminateProcess(self.handle, 0), "STOP_FAILED")
        require(self.kernel.WaitForSingleObject(self.handle, 10000) == 0, "STOP_TIMEOUT")


def _state(root):
    path = safe_child(root, "state.json")
    return read_json(path) if path.exists() else None


def _actual(pid):
    try:
        with ProcessHandle(pid) as process:
            return process.snapshot()
    except BridgeError as error:
        if error.code == "PROCESS_NOT_FOUND":
            return None
        raise


def previous_instance_ended(state, actual):
    """PID reuse proves the recorded instance ended; never stop its replacement."""
    created = state.get("creation_time")
    require(isinstance(created, str) and re.fullmatch(r"[1-9][0-9]*", created), "STATE_INVALID")
    if actual is None:
        return True
    current = actual.get("creation_time")
    require(isinstance(current, str) and re.fullmatch(r"[1-9][0-9]*", current), "PROCESS_QUERY_FAILED")
    return created != current


def status(root):
    if not root.exists():
        return {"ok": True, "action": "status", "installed": False, "running": False}
    manifest = _load_owned(root)
    state = _state(root)
    if not isinstance(state, dict) or state.get("status") == "stopped":
        return {"ok": True, "action": "status", "installed": True, "running": False, "nodes": manifest["node_count"]}
    require(type(state.get("pid")) is int and state["pid"] > 0, "STATE_INVALID")
    actual = _actual(state["pid"])
    matches = identity_matches(state, actual, root, manifest)
    healthy = matches and listeners_match(listener_rows(state["pid"], manifest["ports"]), state["pid"], manifest["ports"])
    return {"ok": True, "action": "status", "installed": True, "running": bool(matches), "identity_matches": bool(matches), "all_listeners_private": bool(healthy), "nodes": manifest["node_count"]}


def start(root):
    manifest = _load_owned(root)
    with operation_lock(root):
        old = _state(root)
        if isinstance(old, dict) and old.get("status") != "stopped":
            require(type(old.get("pid")) is int and old["pid"] > 0, "STATE_INVALID")
            actual = _actual(old["pid"])
            if not previous_instance_ended(old, actual):
                require(identity_matches(old, actual, root, manifest), "PROCESS_IDENTITY_MISMATCH")
                require(listeners_match(listener_rows(old["pid"], manifest["ports"]), old["pid"], manifest["ports"]), "LISTENER_IDENTITY_MISMATCH")
                return {"ok": True, "action": "start", "running": True, "unchanged": True, "nodes": manifest["node_count"]}
        require(not listener_rows(0, manifest["ports"]), "PORT_OCCUPIED")
        reservations = []
        try:
            for port in manifest["ports"]:
                reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                reservations.append(reserved)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    reserved.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                reserved.bind(("127.0.0.1", port))
        except OSError:
            raise BridgeError("PORT_UNAVAILABLE") from None
        finally:
            for reserved in reservations:
                reserved.close()
        _validate_core(root)
        process = None
        try:
            with _child_dll_search(), safe_child(root, "logs/runtime.private.log").open("ab") as log:
                argv = [str((root / "core.exe").resolve()), "-d", str((root / "data").resolve()), "-f", str((root / "config.yaml").resolve())]
                process = subprocess.Popen(argv, cwd=str(root), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            actual = _actual(process.pid)
            require(actual is not None, "CORE_START_FAILED")
            state = {"status": "running", "pid": process.pid, "creation_time": actual["creation_time"], "exe": argv[0], "config": argv[-1], "exe_sha256": manifest["files"]["core.exe"], "config_sha256": manifest["files"]["config.yaml"], "install_id": manifest["install_id"]}
            require(identity_matches(state, actual, root, manifest), "PROCESS_IDENTITY_MISMATCH")
            deadline = time.monotonic() + 25
            healthy = False
            while time.monotonic() < deadline and process.poll() is None:
                rows = listener_rows(process.pid, manifest["ports"])
                if listeners_match(rows, process.pid, manifest["ports"]):
                    healthy = True
                    break
                require(all(r.get("pid") == process.pid and r.get("address") == "127.0.0.1" and r.get("port") in manifest["ports"] for r in rows), "UNEXPECTED_LISTENER")
                time.sleep(0.25)
            require(healthy, "CORE_START_FAILED")
            atomic_json(root / "state.json", state)
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise
    return {"ok": True, "action": "start", "running": True, "unchanged": False, "all_listeners_private": True, "nodes": manifest["node_count"]}


def stop(root):
    manifest = _load_owned(root)
    with operation_lock(root):
        state = _state(root)
        if not isinstance(state, dict) or state.get("status") == "stopped":
            return {"ok": True, "action": "stop", "running": False, "unchanged": True}
        require(type(state.get("pid")) is int and state["pid"] > 0, "STATE_INVALID")
        ended = False
        try:
            with ProcessHandle(state["pid"], terminate=True) as process:
                actual = process.snapshot()
                ended = previous_instance_ended(state, actual)
                if not ended:
                    require(identity_matches(state, actual, root, manifest), "PROCESS_IDENTITY_MISMATCH")
                    process.stop()
        except BridgeError as error:
            if error.code != "PROCESS_NOT_FOUND":
                raise
            ended = previous_instance_ended(state, None)
        if ended:
            require(not listener_rows(0, manifest["ports"]), "PORT_OCCUPIED")
        atomic_json(root / "state.json", {"status": "stopped", "install_id": manifest["install_id"]})
    return {"ok": True, "action": "stop", "running": False, "unchanged": False}


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise BridgeError("CLI_ARGUMENTS")


def main(argv=None):
    try:
        parser = SafeParser(description="Private loopback bridge lifecycle")
        parser.add_argument("--root", type=Path)
        commands = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
        installer = commands.add_parser("install")
        installer.add_argument("--stage", required=True, type=Path)
        for action in ("start", "stop", "status"):
            commands.add_parser(action)
        args = parser.parse_args(argv)
        require(os.name == "nt", "WINDOWS_REQUIRED")
        if args.root is None:
            require(bool(os.environ.get("LOCALAPPDATA")), "LOCALAPPDATA_MISSING")
            root = Path(os.environ["LOCALAPPDATA"]) / "CodexClashZeroOmega/feiniao-bridge"
        else:
            root = args.root
        root = root.absolute()
        safe_child(root.parent, root.name)
        result = install(root, args.stage) if args.action == "install" else globals()[args.action](root)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except BridgeError as error:
        print(json.dumps({"ok": False, "error": error.code}))
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "error": "OPERATION_TIMEOUT"}))
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "INTERRUPTED"}))
    except Exception:
        print(json.dumps({"ok": False, "error": "INTERNAL_ERROR"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
