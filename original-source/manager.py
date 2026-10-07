"""Credential-safe Clash Verge/ZeroOmega backend, configured by portable.py."""
from __future__ import annotations

import argparse
import copy
import ctypes
import datetime as dt
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.request

import yaml

RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
HERE = Path(__file__).resolve().parent
DATA = Path(os.environ['APPDATA']) / 'io.github.clash-verge-rev.clash-verge-rev'
PRIVATE = (Path(os.environ['LOCALAPPDATA']) / 'CodexClashZeroOmega').resolve()
CORE = None
VERGE = None
NODE = RESOURCES / 'node.exe' if getattr(sys, 'frozen', False) else Path(shutil.which('node') or 'node.exe')
NS = 'czo.v1.'
BAK = 'ZeroOmega-全节点-手动选择-Mihomo分流.bak'
TEMPLATE = None
POLICY_OVERRIDE = None
MARKER = '// CZO MANAGED V1'


def run(args, **kwargs):
    args = list(args)
    executable_name = Path(args[0]).name.lower()
    kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
    if executable_name in ('powershell.exe', 'netsh', 'netsh.exe', 'netstat', 'netstat.exe'):
        system32 = Path(os.environ['SystemRoot']) / 'System32'
        args[0] = str(system32 / ('WindowsPowerShell/v1.0/powershell.exe' if executable_name == 'powershell.exe'
                                else executable_name.removesuffix('.exe') + '.exe'))
    if executable_name == 'powershell.exe':
        child_env = {k: v for k, v in (kwargs.get('env') or os.environ).items() if k.upper() != 'PSMODULEPATH'}
        child_env['PSModulePath'] = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules')
        kwargs['env'] = child_env
    frozen = getattr(sys, 'frozen', False)
    if frozen:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.run(args, **kwargs)
    finally:
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(str(RESOURCES))


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf8')
    return hashlib.sha256(value).hexdigest()


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding='utf8')) or {}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path = path.parent.resolve() / path.name
    temp = path.with_name(path.name + '.new')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temp, path)


def atomic_bytes(path, data):
    path = path.parent.resolve() / path.name
    temp = path.with_name(path.name + '.czo-new')
    temp.write_bytes(data)
    os.replace(temp, path)


def api(method, path, payload=None, config=None, timeout=None):
    """Use the existing controller; never create a new public control port."""
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not 0 < timeout <= 300):
        raise ValueError('Invalid local API timeout')
    explicit_deadline = time.monotonic() + timeout if timeout is not None else None
    cfg = config or read_yaml(DATA / 'clash-verge.yaml')
    secret = str(cfg.get('secret', ''))
    body = b'' if payload is None else json.dumps(payload).encode('utf8')
    endpoint = cfg.get('external-controller', '')
    if endpoint:
        host = endpoint.rsplit(':', 1)[0].strip('[]')
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise RuntimeError('Controller is not loopback; stopped')
        req = urllib.request.Request('http://' + endpoint + path, data=body if payload is not None else None,
                                     method=method, headers={'Authorization': 'Bearer ' + secret,
                                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=8 if timeout is None else timeout) as response:
            response_body = response.read()
            return response.status, json.loads(response_body) if response_body else None
    pipe = cfg.get('external-controller-pipe')
    if not pipe or not pipe.startswith('\\\\.\\pipe\\'):
        raise RuntimeError('No supported local controller')
    pipe_wait_ms = 5000 if timeout is None else min(5000, max(1, int(timeout * 1000)))
    if not ctypes.windll.kernel32.WaitNamedPipeW(pipe, pipe_wait_ms):
        raise RuntimeError('Local controller pipe unavailable')
    with open(pipe, 'r+b', buffering=0) as stream:
        deadline = explicit_deadline if explicit_deadline is not None else time.monotonic() + (25 if method == 'PUT' else 8)
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        peek = ctypes.windll.kernel32.PeekNamedPipe
        peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        peek.restype = ctypes.c_int
        request = (method + ' ' + path + ' HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer ' + secret +
                   '\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)) + '\r\n\r\n').encode() + body
        stream.write(request)

        def exact(count):
            data = b''
            while len(data) < count:
                available = ctypes.c_ulong()
                if not peek(handle, None, 0, None, ctypes.byref(available), None):
                    raise RuntimeError('Local API pipe closed before complete response')
                if available.value == 0:
                    if time.monotonic() >= deadline:
                        if timeout is not None:
                            raise TimeoutError('Local API response timed out')
                        raise RuntimeError('Local API response timed out; application state requires recovery verification')
                    time.sleep(0.005)
                    continue
                part = stream.read(min(count - len(data), available.value))
                if not part:
                    raise RuntimeError('Incomplete local API response')
                data += part
            return data

        def line():
            data = b''
            while not data.endswith(b'\r\n'):
                data += exact(1)
                if len(data) > 65536:
                    raise RuntimeError('Oversize API header')
            return data

        status = int(line().split()[1])
        headers = {}
        while True:
            row = line()
            if row == b'\r\n':
                break
            key, value = row.decode().split(':', 1)
            headers[key.lower()] = value.strip()
        if 'content-length' in headers:
            response_body = exact(int(headers['content-length']))
        elif headers.get('transfer-encoding', '').lower() == 'chunked':
            response_body = b''
            while True:
                size = int(line().split(b';')[0].strip(), 16)
                if size == 0:
                    line()
                    break
                response_body += exact(size)
                exact(2)
                if len(response_body) > 32 * 1024 * 1024:
                    raise RuntimeError('Oversize API body')
        else:
            response_body = b''
        if not 200 <= status < 300:
            raise RuntimeError('Local API failed: HTTP ' + str(status))
        return status, json.loads(response_body) if response_body else None


def current():
    profiles = read_yaml(DATA / 'profiles.yaml')
    item = next(x for x in profiles['items'] if x['uid'] == profiles['current'])
    script_id = item.get('option', {}).get('script')
    if not script_id:
        raise RuntimeError('Active subscription has no dedicated script; stopped')
    script_item = next(x for x in profiles['items'] if x['uid'] == script_id)
    script = DATA / 'profiles' / script_item['file']
    if script.parent.resolve() != (DATA / 'profiles').resolve():
        raise RuntimeError('Unexpected script path')
    # URL never leaves this process. A changed source cannot inherit another source's ports.
    source = digest([item['uid'], item.get('url', ''), item.get('type')])
    return profiles, item, script, source


def reload_file(path, controller_config):
    # Core path API accepts only its approved home directories. Use the native
    # payload API rather than weakening safe-path restrictions for private staging.
    return api('PUT', '/configs', {'payload': Path(path).read_text(encoding='utf8')}, config=controller_config)


def unmanaged(config):
    result = copy.deepcopy(config)
    result['proxy-groups'] = [x for x in result.get('proxy-groups', []) if not x['name'].startswith(NS)]
    if 'listeners' in result:
        result['listeners'] = [x for x in result['listeners'] if not x['name'].startswith(NS)]
        if not result['listeners']:
            result.pop('listeners')
    if 'sub-rules' in result:
        result['sub-rules'] = {k: v for k, v in result['sub-rules'].items() if not k.startswith(NS)}
        if not result['sub-rules']:
            result.pop('sub-rules')
    return result


def proxy_snapshot(config=None):
    return api('GET', '/proxies', config=config)[1]['proxies']


def resolve_action(name, proxies, seen=None):
    if name in ('DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'):
        return name
    seen = set() if seen is None else seen
    if name in seen or name not in proxies:
        raise RuntimeError('Unresolved group chain; stopped')
    seen.add(name)
    value = proxies[name]
    if value.get('type') in ('Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Relay'):
        selected = value.get('now')
        if not selected:
            raise RuntimeError('Ambiguous group chain; stopped')
        return resolve_action(selected, proxies, seen)
    return 'PROXY'


def excluded_ports():
    result = set()
    for family in ('ipv4', 'ipv6'):
        proc = run(['netsh', 'interface', family, 'show', 'excludedportrange', 'protocol=tcp'], capture_output=True, timeout=10)
        if proc.returncode:
            raise RuntimeError('Cannot inspect reserved ports')
        for line in proc.stdout.decode(errors='replace').splitlines():
            match = re.fullmatch(r'\s*(\d+)\s+(\d+)\s*\*?\s*', line)
            if match:
                result.update(range(int(match[1]), int(match[2]) + 1))
    return result


def free_port(port):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def endpoint_tag(node):
    text = '\0'.join([str(node.get('type', '')), str(node.get('server', '')), str(node.get('port', ''))])
    a, b = 2166136261, 2246822507
    units = text.encode('utf-16le')
    for i in range(0, len(units), 2):
        char = int.from_bytes(units[i:i+2], 'little')
        a = ((a ^ char) * 16777619) & 0xffffffff
        b = ((b ^ char) * 3266489909) & 0xffffffff
    return f'{a:08x}{b:08x}'


def private_backup(paths):
    target = PRIVATE / 'backups' / dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target.mkdir(parents=True, exist_ok=False)
    files = []
    for path in paths:
        if path.is_file():
            relative = path.relative_to(DATA)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            files.append({'file': str(relative), 'sha256': digest(path.read_bytes())})
    write_json(target / 'manifest.json', {'files': files})
    return target


def run_engine(base, metadata, stage):
    write_json(stage / 'input.json', base)
    write_json(stage / 'metadata.json', metadata)
    proc = run([str(NODE), str(RESOURCES / 'js-runner.js'), str(RESOURCES / 'enhancement.js'),
                           str(stage / 'input.json'), str(stage / 'metadata.json'), str(stage / 'output.json')], capture_output=True, timeout=10)
    if proc.returncode:
        raise RuntimeError('Routing transform rejected this configuration')
    return json.loads((stage / 'output.json').read_text(encoding='utf8'))


def core_validate(config, stage):
    file = stage / 'candidate.yaml'
    file.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf8')
    proc = run([str(CORE), '-t', '-d', str(DATA), '-f', str(file)], capture_output=True, timeout=45)
    # Raw diagnostics are restricted; parsing errors may include private input.
    (stage / 'core-validation.private.txt').write_bytes(proc.stdout + proc.stderr)
    if proc.returncode:
        raise RuntimeError('Mihomo final configuration validation failed; private diagnostics retained')
    return file


def write_map(entries):
    safe = [{k: e[k] for k in ('id', 'subscriptionCode', 'provider', 'nodeName', 'profileName', 'port', 'active', 'status')} for e in entries]
    write_json(HERE / 'node-port-map.json', safe)
    (HERE / '节点与端口映射表.md').write_text('| 完整节点名 | ZeroOmega 情景模式 | SOCKS5 端口 | 状态 |\n|---|---|---:|---|\n' +
        ''.join('| ' + e['nodeName'].replace('|', '\\|') + ' | ' + e['profileName'].replace('|', '\\|') + ' | ' + str(e['port']) + ' | ' + e['status'] + ' |\n' for e in entries), encoding='utf8')


def prepare():
    from zeroomega import build_backup, validate_backup
    version = run([str(CORE), '-v'], capture_output=True, timeout=8)
    if version.returncode or 'Mihomo Meta v1.19.32 ' not in version.stdout.decode(errors='replace'):
        raise RuntimeError('Core version changed; isolation semantics require a new audit')
    verge_version = run(['powershell.exe', '-NoProfile', '-Command',
                                    "(Get-Item -LiteralPath '" + str(VERGE).replace("'", "''") + "').VersionInfo.ProductVersion"],
                                   capture_output=True, timeout=8)
    if verge_version.returncode or verge_version.stdout.decode(errors='replace').strip() != '2.5.7':
        raise RuntimeError('Clash Verge version changed; extension order requires a new audit')
    profiles, item, script, source = current()
    runtime = read_yaml(DATA / 'clash-verge.yaml')
    live = api('GET', '/configs', config=runtime)[1]
    if live.get('mode') != 'rule' or runtime.get('mode') != 'rule':
        raise RuntimeError('Rule mode is required; mode was not changed')
    base = unmanaged(runtime)
    if base.get('proxy-providers'):
        raise RuntimeError('Provider configuration needs a separate audit; stopped without rebinding ports')
    registry_path = PRIVATE / 'registry.json'
    registry = json.loads(registry_path.read_text(encoding='utf8')) if registry_path.exists() else {'entries': [], 'policies': {}}
    entries = registry['entries']
    known = {e['id']: e for e in entries}
    old_entries = copy.deepcopy(entries)
    used = {e['port'] for e in entries}
    reserved = excluded_ports()
    proxies = proxy_snapshot(runtime)
    groups = base.get('proxy-groups', [])
    policy = registry['policies'].get(source)
    if POLICY_OVERRIDE is not None:
        if set(POLICY_OVERRIDE) != {g['name'] for g in groups} or any(v not in ('DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'PROXY', 'COMPATIBLE') for v in POLICY_OVERRIDE.values()):
            raise RuntimeError('Invalid reviewed group policy')
        policy = dict(POLICY_OVERRIDE)
        policy.update({p['name']: 'PROXY' for p in base.get('proxies', [])})
        registry['policies'][source] = policy
    elif policy is None:
        policy = {g['name']: resolve_action(g['name'], proxies) for g in groups}
        policy.update({p['name']: 'PROXY' for p in base.get('proxies', [])})
        registry['policies'][source] = policy
    for e in entries:
        e['active'] = False
        e['status'] = '失效/非当前订阅'
    ignored = []
    raw_names = set()
    for node in base.get('proxies', []):
        name = node.get('name')
        if not isinstance(name, str) or name in raw_names:
            raise RuntimeError('Duplicate or invalid node name')
        raw_names.add(name)
        if node.get('type') in ('direct', 'reject', 'reject-drop', 'pass', 'compatible'):
            ignored.append({'name': name, 'reason': '内置动作'})
            continue
        if re.fullmatch(r'\*.*官网.*\*', name) or any(tag in name for tag in ('剩余流量', '套餐到期', '订阅更新', '到期时间')):
            ignored.append({'name': name, 'reason': '订阅公告'})
            continue
        if name not in proxies:
            raise RuntimeError('Runtime/API node set mismatch')
        if node.get('dialer-proxy'):
            raise RuntimeError('Chained node requires separate isolation audit')
        identity = digest([source, 'inline', name])
        if identity not in known:
            port = next((n for n in range(20000, 45000) if n not in used and n not in reserved and free_port(n)), None)
            if port is None:
                raise RuntimeError('No free non-reserved port')
            used.add(port)
            profile_name = name
            if name.startswith('_') or any(ord(c) < 32 for c in name) or name in ('direct', 'system', 'auto_detect'):
                profile_name = '线路 ' + ''.join(c if ord(c) >= 32 else ' ' for c in name)
            if any(x['profileName'] == profile_name for x in entries):
                profile_name += ' [' + identity[:8] + ']'
            entry = {'id': identity, 'subscriptionCode': 'sub-' + source[:12], 'provider': 'inline', 'nodeName': name,
                     'profileName': profile_name, 'port': port, 'active': True, 'status': '有效'}
            entries.append(entry)
            known[identity] = entry
        known[identity]['active'] = True
        known[identity]['status'] = '有效'
        known[identity]['endpointTag'] = endpoint_tag(node)
    if not any(e['active'] for e in entries):
        raise RuntimeError('No real nodes found')
    if len(used) != len(entries) or any(p in reserved for p in used):
        raise RuntimeError('Port registry conflict; ports were not reassigned')
    unrelated_ports = {base.get(k) for k in ('port', 'socks-port', 'mixed-port', 'redir-port', 'tproxy-port') if base.get(k)}
    unrelated_ports.update(x.get('port') for x in base.get('listeners', []))
    if used & unrelated_ports:
        raise RuntimeError('An existing main/unmanaged listener conflicts with a reserved browser port')
    original_path = PRIVATE / 'original-scripts' / (script.stem + '.js')
    original_path.parent.mkdir(parents=True, exist_ok=True)
    current_script = script.read_text(encoding='utf8')
    installed_path = PRIVATE / 'installed.json'
    installed = json.loads(installed_path.read_text(encoding='utf8')) if installed_path.exists() else {}
    if current_script.startswith(MARKER):
        record = installed.get('scripts', {}).get(str(script))
        if not record or digest(script.read_bytes()) != record['hash'] or not original_path.exists():
            raise RuntimeError('Managed script changed externally; stopped to preserve edits')
        original = original_path.read_text(encoding='utf8')
    else:
        original = current_script
        if original_path.exists() and original_path.read_text(encoding='utf8') != original:
            raise RuntimeError('Original script changed; backup review required')
        atomic_bytes(original_path, script.read_bytes())
    # The current dedicated script is a verified pass-through. More complex
    # subscription scripts require native full-pipeline review before installation.
    plain = re.sub(r'/\*.*?\*/|//[^\n]*', '', original, flags=re.S)
    if not re.fullmatch(r'\s*function\s+main\s*\(\s*config\s*,\s*profileName\s*\)\s*\{\s*return\s+config\s*;?\s*\}\s*', plain):
        raise RuntimeError('Nontrivial subscription script requires final-pipeline audit; existing script preserved')
    metadata = {'entries': entries, 'policy': policy, 'fallback': 'PROXY'}
    stage = PRIVATE / 'staging' / dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    stage.mkdir(parents=True, exist_ok=False)
    candidate = run_engine(base, metadata, stage)
    if unmanaged(candidate) != base:
        raise RuntimeError('Unmanaged configuration changed; stopped')
    second = run_engine(candidate, metadata, stage / 'idempotency')
    if second != candidate:
        raise RuntimeError('Transform is not idempotent')
    engine = (RESOURCES / 'enhancement.js').read_text(encoding='utf8')
    generated = MARKER + '\nfunction czoPreviousMain(config, profileName) {\n' + original + '\nreturn main(config, profileName);\n}\n' + engine + '\nconst czoMetadata = ' + json.dumps(metadata, ensure_ascii=False) + ';\nfunction main(config, profileName) { return czoTransform(czoPreviousMain(config, profileName), czoMetadata); }\n'
    (stage / 'installed-extension.js').write_text(generated, encoding='utf8')
    # Validate the actual wrapper as well as the standalone routing engine.
    wrapper_test = "const fs=require('fs'),vm=require('vm');let c=vm.createContext({});vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c,{timeout:4000});c.config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));fs.writeFileSync(process.argv[3],JSON.stringify(vm.runInContext('main(config,\"subscription\")',c,{timeout:4000})));"
    check = run([str(NODE), '-e', wrapper_test, str(stage / 'installed-extension.js'), str(stage / 'input.json'), str(stage / 'wrapper-output.json')], capture_output=True, timeout=10)
    if check.returncode or json.loads((stage / 'wrapper-output.json').read_text(encoding='utf8')) != candidate:
        raise RuntimeError('Original subscription script needs final-pipeline review; stopped')
    config_path = core_validate(candidate, stage)
    options = build_backup(entries, TEMPLATE if TEMPLATE and TEMPLATE.exists() else None)
    bak_check = validate_backup(options, entries)
    # Persist reservations before delivering any backup; failed/repeated runs never recycle ports.
    write_json(PRIVATE / 'registry.json', registry)
    write_json(HERE / BAK, options)
    write_map(entries)
    atomic_bytes(HERE / '待同步的订阅扩展.js', (stage / 'installed-extension.js').read_bytes())
    changes = {'new': len([e for e in entries if e['id'] not in {x['id'] for x in old_entries}]),
               'active': sum(e['active'] for e in entries), 'inactive': sum(not e['active'] for e in entries),
               'totalProxyEntries': len(base.get('proxies', [])), 'ignored': ignored,
               'rules': len(base.get('rules', [])), 'browserRules': len(candidate['sub-rules'][NS + 'rules']),
               'portRange': [min(used), max(used)], 'bakCheck': bak_check}
    write_json(stage / 'registry.json', registry)
    prepared = {'stage': str(stage), 'script': str(script), 'scriptBeforeHash': digest(script.read_bytes()),
                'runtimeBeforeHash': digest((DATA / 'clash-verge.yaml').read_bytes()), 'source': source,
                'current': profiles['current'], 'config': str(config_path), 'changes': changes,
                'liveControl': {k: live.get(k) for k in ('mode', 'port', 'socks-port', 'mixed-port', 'redir-port', 'tproxy-port', 'allow-lan', 'bind-address', 'ipv6', 'tun')},
                'selectors': {k: v.get('now') for k, v in proxies.items() if v.get('type') == 'Selector' and not k.startswith(NS)}}
    write_json(PRIVATE / 'prepared.json', prepared)
    live_status = 'prepared-not-applied'
    record = installed.get('scripts', {}).get(str(script))
    if record and current_script.startswith(MARKER) and digest(script.read_bytes()) == record['hash']:
        live_status = 'installed-and-live' if candidate == runtime else 'installed-with-pending-update'
    write_json(HERE / '验证摘要.json', {'status': live_status, 'preparedUpdateValidated': True, 'changes': changes,
                                      'unmanagedConfigEqual': True, 'idempotent': True, 'coreValidated': True})
    return prepared


def install(prepared):
    runtime = read_yaml(DATA / 'clash-verge.yaml')
    profiles, item, script, source = current()
    if source != prepared['source'] or digest(script.read_bytes()) != prepared['scriptBeforeHash'] or digest((DATA / 'clash-verge.yaml').read_bytes()) != prepared['runtimeBeforeHash']:
        raise RuntimeError('Configuration changed since validation; run update again')
    controls = api('GET', '/configs', config=runtime)[1]
    if controls.get('mode') != 'rule' or any(controls.get(k) != v for k, v in prepared.get('liveControl', {}).items()):
        raise RuntimeError('Live main routing controls changed since validation; stopped without applying')
    stage = Path(prepared['stage'])
    entries = json.loads((stage / 'registry.json').read_text(encoding='utf8'))['entries']
    owners, core_ids = listener_owners(), core_process_ids()
    if not core_ids:
        raise RuntimeError('Running core process not found')
    for e in entries:
        if any(pid not in core_ids for endpoint, pid in owners.get(e['port'], [])):
            raise RuntimeError('Reserved registry port is occupied by another process; stopped')
    backup = private_backup([script, DATA / 'clash-verge.yaml', DATA / 'profiles.yaml', DATA / 'config.yaml', DATA / 'verge.yaml', DATA / 'dns_config.yaml'])
    installed_path = PRIVATE / 'installed.json'
    previous = json.loads(installed_path.read_text(encoding='utf8')) if installed_path.exists() else {'scripts': {}}
    previous_state = copy.deepcopy(previous)
    # Selector changes made since preparation remain authoritative.
    before_proxies = proxy_snapshot(runtime)
    selectors = {k: v.get('now') for k, v in before_proxies.items() if v.get('type') == 'Selector' and not k.startswith(NS)}
    new_script = (stage / 'installed-extension.js').read_bytes()
    recovery = {'phase': 'applying', 'backup': str(backup), 'script': str(script), 'stage': str(stage)}
    write_json(PRIVATE / 'recovery.json', recovery)
    try:
        atomic_bytes(script, new_script)
        reload_file(prepared['config'], runtime)
        time.sleep(1)
        owners = listener_owners()
        missing = [e['port'] for e in entries if owners.get(e['port']) != [('127.0.0.1:' + str(e['port']), next(iter(core_ids)))] ] if len(core_ids) == 1 else [e['port'] for e in entries if not any(endpoint == '127.0.0.1:' + str(e['port']) and pid in core_ids for endpoint, pid in owners.get(e['port'], []))]
        if missing:
            raise RuntimeError('One or more listener ports failed to bind')
        if core_process_ids() != core_ids:
            raise RuntimeError('Unexpected core process change')
        after = proxy_snapshot(runtime)
        for name, selected in selectors.items():
            if after.get(name, {}).get('now') != selected:
                raise RuntimeError('An original selector changed')
        if api('GET', '/configs', config=runtime)[1].get('mode') != 'rule':
            raise RuntimeError('Core mode changed')
        atomic_bytes(DATA / 'clash-verge.yaml', Path(prepared['config']).read_bytes())
        previous['scripts'][str(script)] = {'hash': digest(new_script), 'original': str(PRIVATE / 'original-scripts' / (script.stem + '.js'))}
        previous['latestBackup'] = str(backup)
        previous['lastStage'] = str(stage)
        previous['lastAppliedAt'] = dt.datetime.now().isoformat(timespec='seconds')
        write_json(installed_path, previous)
        atomic_bytes(PRIVATE / 'registry.json', (stage / 'registry.json').read_bytes())
        write_json(PRIVATE / 'recovery.json', {**recovery, 'phase': 'complete'})
    except Exception:
        failures = []
        for label, restore in [
            ('script', lambda: atomic_bytes(script, (backup / script.relative_to(DATA)).read_bytes())),
            ('core', lambda: reload_file(backup / 'clash-verge.yaml', runtime)),
            ('runtime-file', lambda: atomic_bytes(DATA / 'clash-verge.yaml', (backup / 'clash-verge.yaml').read_bytes())),
            ('state', lambda: write_json(installed_path, previous_state))]:
            try:
                restore()
            except Exception:
                failures.append(label)
        write_json(PRIVATE / 'recovery.json', {**recovery, 'phase': 'recovery-required' if failures else 'restored', 'failedStages': failures})
        if failures:
            raise RuntimeError('Application failed and recovery is incomplete; private recovery manifest retained')
        raise
    result = {'status': 'installed-and-live', 'listeners': len(entries), 'backup': str(backup)}
    try:
        write_json(HERE / '验证摘要.json', {'status': 'installed-and-live', 'changes': prepared['changes'], 'coreValidated': True,
                   'unmanagedConfigEqual': True, 'idempotent': True, 'originalSelectorsEqual': True,
                   'listenersVerified': len(entries), 'appliedAt': previous['lastAppliedAt']})
    except OSError:
        # The installation already completed; an export failure must not undo or misreport it.
        result['summaryWarning'] = '已写回并生效，但验证摘要导出失败。'
    return result


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            return True
    except OSError:
        return False


def listener_owners():
    proc = run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, timeout=10)
    if proc.returncode:
        raise RuntimeError('Cannot inspect listening port owners')
    found = {}
    for row in proc.stdout.decode(errors='replace').splitlines():
        parts = row.split()
        if len(parts) >= 5 and parts[0] == 'TCP' and parts[-2] == 'LISTENING':
            endpoint, pid = parts[1], int(parts[-1])
            port = int(endpoint.rsplit(':', 1)[1])
            found.setdefault(port, []).append((endpoint, pid))
    return found


def core_process_ids():
    proc = run(['powershell.exe', '-NoProfile', '-Command',
                           "@(Get-Process -Name 'verge-mihomo' -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq '" + str(CORE).replace("'", "''") + "'} | ForEach-Object {$_.Id}) | ConvertTo-Json -Compress"], capture_output=True, timeout=10)
    value = proc.stdout.decode('utf8', errors='replace').strip()
    if proc.returncode or not value:
        raise RuntimeError('Cannot inspect running core PID')
    parsed = json.loads(value)
    return set(parsed if isinstance(parsed, list) else [parsed])


def rollback():
    state = json.loads((PRIVATE / 'installed.json').read_text(encoding='utf8'))
    runtime = read_yaml(DATA / 'clash-verge.yaml')
    # Preserve the current subscription, current main route, and other people's later edits.
    for path, record in state['scripts'].items():
        target = Path(path)
        if digest(target.read_bytes()) != record['hash']:
            raise RuntimeError('Extension has external edits; rollback stopped')
    stage = PRIVATE / 'rollback' / dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    stage.mkdir(parents=True, exist_ok=False)
    candidate = unmanaged(runtime)
    path = core_validate(candidate, stage)
    backup = private_backup([Path(p) for p in state['scripts']] + [DATA / 'clash-verge.yaml'])
    previous_state = copy.deepcopy(state)
    recovery = {'phase': 'rolling-back', 'backup': str(backup)}
    write_json(PRIVATE / 'recovery.json', recovery)
    try:
        for script, record in state['scripts'].items():
            atomic_bytes(Path(script), Path(record['original']).read_bytes())
        reload_file(path, runtime)
        atomic_bytes(DATA / 'clash-verge.yaml', path.read_bytes())
        state['rolledBackAt'] = dt.datetime.now().isoformat(timespec='seconds')
        state['scripts'] = {}
        write_json(PRIVATE / 'installed.json', state)
        write_json(PRIVATE / 'recovery.json', {**recovery, 'phase': 'rolled-back'})
    except Exception:
        failures = []
        for script in previous_state['scripts']:
            try:
                atomic_bytes(Path(script), (backup / Path(script).relative_to(DATA)).read_bytes())
            except Exception:
                failures.append('script')
        try:
            reload_file(backup / 'clash-verge.yaml', runtime)
            atomic_bytes(DATA / 'clash-verge.yaml', (backup / 'clash-verge.yaml').read_bytes())
            write_json(PRIVATE / 'installed.json', previous_state)
        except Exception:
            failures.append('core-or-state')
        write_json(PRIVATE / 'recovery.json', {**recovery, 'phase': 'recovery-required' if failures else 'restored', 'failedStages': failures})
        if failures:
            raise RuntimeError('Rollback recovery is incomplete; private recovery manifest retained')
        raise
    # Keep registry forever: rollback must not recycle old ports.
    return {'status': 'rolled-back', 'registryRetained': True}


def sock_read(sock, count):
    result = b''
    while len(result) < count:
        part = sock.recv(count - len(result))
        if not part:
            raise RuntimeError('Connection closed')
        result += part
    return result


def probe(entry, domain, expected):
    raw = socket.create_connection(('127.0.0.1', entry['port']), timeout=12)
    try:
        raw.sendall(b'\x05\x01\x00')
        if sock_read(raw, 2) != b'\x05\x00':
            raise RuntimeError('SOCKS negotiation failed')
        encoded = domain.encode('idna')
        raw.sendall(b'\x05\x01\x00\x03' + bytes([len(encoded)]) + encoded + struct.pack('!H', 443))
        reply = sock_read(raw, 4)
        if reply[1] != 0:
            raise RuntimeError('SOCKS connection failed')
        if reply[3] == 1:
            sock_read(raw, 6)
        elif reply[3] == 4:
            sock_read(raw, 18)
        elif reply[3] == 3:
            sock_read(raw, sock_read(raw, 1)[0] + 2)
        context = ssl.create_default_context()
        with context.wrap_socket(raw, server_hostname=domain) as tls:
            connections = api('GET', '/connections')[1]['connections']
            name = NS + 'in.' + entry['id'][:20]
            records = [x for x in connections if x.get('metadata', {}).get('inboundName') == name and x.get('metadata', {}).get('host') == domain]
            # Some core versions expose inbound name under a different metadata field.
            if not records:
                records = [x for x in connections if x.get('metadata', {}).get('sourcePort') == str(tls.getsockname()[1]) and x.get('metadata', {}).get('host') == domain]
            chain = records[-1].get('chains', []) if records else []
            route_ok = bool(records) and (('DIRECT' in chain) if expected == 'DIRECT' else (entry['nodeName'] in chain and 'DIRECT' not in chain))
            tls.sendall(('HEAD / HTTP/1.1\r\nHost: ' + domain + '\r\nConnection: close\r\n\r\n').encode())
            response = tls.recv(1024).split(b'\r\n', 1)[0].decode('ascii', errors='replace')
            return {'node': entry['nodeName'], 'port': entry['port'], 'target': domain, 'expected': expected,
                    'passed': route_ok, 'chain': chain, 'rule': records[-1].get('rule') if records else None,
                    'rulePayload': records[-1].get('rulePayload') if records else None, 'httpStatusLine': response}
    except Exception as error:
        return {'node': entry['nodeName'], 'port': entry['port'], 'target': domain, 'expected': expected,
                'passed': False, 'errorType': type(error).__name__}
    finally:
        raw.close()


def tests():
    entries = json.loads((PRIVATE / 'registry.json').read_text(encoding='utf8'))['entries']
    active = [x for x in entries if x['active']]
    first = next((x for x in active if x['nodeName'] == '日本621 中继'), active[0])
    second = next((x for x in active if '美国815' in x['nodeName']), active[-1])
    results = [probe(first, 'www.google.com', 'PROXY'), probe(second, 'www.google.com', 'PROXY'), probe(first, 'www.baidu.com', 'DIRECT')]
    write_json(HERE / '低流量测试.json', results)
    return results


def main():
    sys.stdout.reconfigure(encoding='utf8')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'apply-prepared', 'update', 'rollback', 'test', 'status'])
    parser.add_argument('--approved-reload', action='store_true', help='Use only after explicit approval of potential interruption')
    args = parser.parse_args()
    if args.action in ('prepare', 'update'):
        prepared = prepare()
        print(json.dumps({'status': 'validated', **prepared['changes']}, ensure_ascii=False))
        if args.action == 'prepare':
            return
    elif args.action == 'apply-prepared':
        prepared = json.loads((PRIVATE / 'prepared.json').read_text(encoding='utf8'))
    if args.action in ('update', 'apply-prepared', 'rollback'):
        if not args.approved_reload:
            answer = input('安装/回滚会热重载 Mihomo，可能短暂中断连接；不会退出 Clash。确认请输入 APPLY: ')
            if answer != 'APPLY':
                print('已保留校验后的文件，未应用。')
                return
        result = rollback() if args.action == 'rollback' else install(prepared)
    elif args.action == 'test':
        result = tests()
    elif args.action == 'status':
        result = json.loads((HERE / '验证摘要.json').read_text(encoding='utf8'))
    else:
        return
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No traceback / configuration values / API response body in public output.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('停止: ' + message)
        sys.exit(1)
