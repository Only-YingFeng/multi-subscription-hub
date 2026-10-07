"""Explicit real-core test: synthetic data and an owned, copied Mihomo process only.

This script is intentionally excluded from run-tests.py. It never reads Clash's
real data directory, and an HTTP audit hook refuses every controller except the
loopback port currently owned by the Popen process started here.
"""
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import yaml
from test_support import APP_DIR, load_backend

PROTECTED_PID = 33368
SUMMARY_PATH = APP_DIR.parent.parent / 'outputs' / 'clash-zeroomega-app' / 'real-core-transaction-summary.json'


def check(condition, name, results):
    if not condition:
        raise RuntimeError(name)
    results.append({'case': name, 'passed': True})


def processes(m):
    script = ("[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
              "@(Get-Process -Name 'clash-verge','verge-mihomo' -ErrorAction SilentlyContinue | "
              "ForEach-Object {[pscustomobject]@{pid=$_.Id;name=$_.ProcessName;path=$_.Path;"
              "startTicks=$_.StartTime.ToUniversalTime().Ticks}}) | ConvertTo-Json -Compress")
    result = m.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('process discovery failed')
    value = json.loads(result.stdout.decode('utf8', errors='replace'))
    return value if isinstance(value, list) else [value]


def unused_loopback_port(used):
    for _ in range(10):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        if port not in used:
            used.add(port)
            return port
    raise RuntimeError('could not allocate isolated loopback ports')


def all_tcp_owners(m):
    """Inspect all TCP families, including IPv6, without changing any listeners."""
    result = m.run(['netstat', '-ano'], capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError('all-family listener inspection failed')
    found = {}
    for row in result.stdout.decode(errors='replace').splitlines():
        parts = row.split()
        if len(parts) >= 5 and parts[0] == 'TCP' and parts[-2] == 'LISTENING':
            endpoint, pid = parts[1], int(parts[-1])
            found.setdefault(int(endpoint.rsplit(':', 1)[1]), []).append((endpoint, pid))
    return found


def execute(summary):
    results = summary['tests']
    with TemporaryDirectory(prefix='czo-real-core-') as temporary:
        root = Path(temporary).resolve()
        m = load_backend(root)
        summary['failureStage'] = 'discover-programs'
        rows = processes(m)
        protected = next((row for row in rows if row['pid'] == PROTECTED_PID and row['name'] == 'verge-mihomo'), None)
        if not protected or not protected.get('path'):
            raise RuntimeError('specified production process could not be verified')
        source_core = Path(protected['path']).resolve()
        verge_rows = [row for row in rows if row['name'] == 'clash-verge' and row.get('path')
                      and Path(row['path']).resolve().parent == source_core.parent]
        if len(verge_rows) != 1:
            raise RuntimeError('could not verify one corresponding running Clash program')
        version = m.run([str(source_core), '-v'], capture_output=True, timeout=10)
        if version.returncode or not re.search(rb'Mihomo Meta v1\.19\.32 ', version.stdout):
            raise RuntimeError('real core version differs from the verified version')
        summary['coreVersion'] = '1.19.32'

        program, data, private, output = [root / name for name in ('program', 'data', 'private', 'output')]
        for directory in (program, data / 'profiles', private, output):
            directory.mkdir(parents=True)
        copied_core = program / 'verge-mihomo.exe'
        shutil.copy2(source_core, copied_core)
        check(m.digest(source_core.read_bytes()) == m.digest(copied_core.read_bytes()),
              'only the verified core binary was copied into the isolated program directory', results)
        m.CORE, m.VERGE = copied_core, Path(verge_rows[0]['path'])
        m.DATA, m.PRIVATE, m.HERE, m.RESOURCES = data, private, output, APP_DIR
        node = shutil.which('node')
        if not node:
            raise RuntimeError('local Node runtime unavailable')
        m.NODE, m.TEMPLATE, m.POLICY_OVERRIDE = Path(node), None, None

        used = set()
        control_port, main_port = unused_loopback_port(used), unused_loopback_port(used)
        controller = '127.0.0.1:' + str(control_port)
        base = {'mode': 'rule', 'mixed-port': main_port, 'allow-lan': False,
                'bind-address': '127.0.0.1', 'ipv6': False, 'log-level': 'silent',
                'external-controller': controller, 'secret': 'synthetic-' + uuid.uuid4().hex,
                'tun': {'enable': False}, 'dns': {'enable': False},
                'profile': {'store-selected': True},
                'proxies': [
                    {'name': 'synthetic-A', 'type': 'socks5', 'server': '127.0.0.1', 'port': 9},
                    {'name': 'synthetic-B', 'type': 'socks5', 'server': '127.0.0.1', 'port': 9}],
                'proxy-groups': [{'name': 'SYNTHETIC_MAIN', 'type': 'select', 'proxies': ['synthetic-A', 'synthetic-B']}],
                'rules': ['DOMAIN,domestic.invalid,DIRECT', 'DOMAIN,blocked.invalid,REJECT', 'MATCH,SYNTHETIC_MAIN']}
        runtime = data / 'clash-verge.yaml'
        runtime.write_text(yaml.safe_dump(base, sort_keys=False), encoding='utf8')
        profiles = {'current': 'synthetic-source', 'items': [
            {'uid': 'synthetic-source', 'type': 'remote', 'url': 'https://example.invalid/synthetic', 'option': {'script': 'synthetic-script'}},
            {'uid': 'synthetic-script', 'type': 'script', 'file': 'synthetic.js'}]}
        profiles_path = data / 'profiles.yaml'
        profiles_path.write_text(yaml.safe_dump(profiles), encoding='utf8')
        profiles_before = profiles_path.read_bytes()
        script = data / 'profiles' / 'synthetic.js'
        original_script = b'function main(config, profileName) { return config; }\r\n'
        script.write_bytes(original_script)

        proc = None
        log = None
        api_calls = []
        # Only this test process changes its urllib opener. All requests remain real.
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

        def audit(event, arguments):
            if event != 'urllib.Request':
                return
            url = urllib.parse.urlsplit(arguments[0])
            if (proc is None or proc.poll() is not None or url.scheme != 'http'
                or url.hostname != '127.0.0.1' or url.port != control_port
                or m.listener_owners().get(control_port) != [(controller, proc.pid)]):
                summary['forbiddenControllerAttempts'] += 1
                raise RuntimeError('refused a controller not owned by this test process')
            api_calls.append((arguments[3] or 'GET', url.path))

        sys.addaudithook(audit)
        try:
            summary['failureStage'] = 'start-isolated-core'
            log = (private / 'isolated-core.private.log').open('wb')
            proc = subprocess.Popen([str(copied_core), '-d', str(data), '-f', str(runtime)],
                                    stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            summary['isolatedPid'] = proc.pid
            if proc.pid == PROTECTED_PID:
                raise RuntimeError('isolated process identity unexpectedly matches protected process')
            deadline = time.monotonic() + 20
            while True:
                if proc.poll() is not None:
                    raise RuntimeError('isolated core exited during startup')
                owners = m.listener_owners()
                if owners.get(control_port) == [(controller, proc.pid)] and owners.get(main_port) == [('127.0.0.1:' + str(main_port), proc.pid)]:
                    try:
                        m.api('GET', '/configs')
                        break
                    except (urllib.error.URLError, TimeoutError, ConnectionError):
                        pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('isolated core did not become ready within the bounded wait')
                time.sleep(0.15)
            check(m.core_process_ids() == {proc.pid}, 'real executable-path process discovery finds only the owned copied core', results)
            summary['processDiscoveryMocked'] = False
            summary['failureStage'] = 'select-original-main-group'
            m.api('PUT', '/proxies/SYNTHETIC_MAIN', {'name': 'synthetic-B'})
            before_selectors = {name: item.get('now') for name, item in m.proxy_snapshot().items() if item.get('type') == 'Selector'}
            before_controls = m.api('GET', '/configs')[1]
            check(before_selectors.get('SYNTHETIC_MAIN') == 'synthetic-B', 'a nondefault original selector was selected through the real isolated API', results)

            summary['failureStage'] = 'prepare'
            prepared = m.prepare()
            registry_before = (private / 'registry.json').read_bytes()
            entries = json.loads(registry_before)['entries']
            check(script.read_bytes() == original_script and m.read_yaml(runtime) == base,
                  'real preparation preserves original script and runtime configuration', results)
            check(all(not m.listener_owners().get(entry['port']) for entry in entries),
                  'preparation does not bind generated entry ports', results)

            summary['failureStage'] = 'install'
            installed = m.install(prepared)
            owners = all_tcp_owners(m)
            check(installed['status'] == 'installed-and-live' and len(entries) == 2
                  and all(owners.get(entry['port']) == [('127.0.0.1:' + str(entry['port']), proc.pid)] for entry in entries),
                  'real payload reload creates every dedicated listener only on IPv4 loopback', results)
            check(script.read_bytes().startswith(m.MARKER.encode()) and m.unmanaged(m.read_yaml(runtime)) == base,
                  'real installed extension and runtime preserve all unmanaged main configuration', results)
            after_selectors = m.proxy_snapshot()
            check(all(after_selectors.get(name, {}).get('now') == selected for name, selected in before_selectors.items()),
                  'real installation preserves every original selector selection', results)
            control_keys = tuple(prepared['liveControl'])
            check(all(m.api('GET', '/configs')[1].get(key) == before_controls.get(key) for key in control_keys),
                  'real installation preserves original live main controls', results)
            check(profiles_path.read_bytes() == profiles_before, 'subscription profile metadata is never rewritten', results)

            summary['failureStage'] = 'rollback'
            rolled_back = m.rollback()
            check(rolled_back['status'] == 'rolled-back' and script.read_bytes() == original_script and m.read_yaml(runtime) == base,
                  'real rollback restores exact script bytes and original structural runtime configuration', results)
            owners = all_tcp_owners(m)
            check(all(not any(pid == proc.pid for _, pid in owners.get(entry['port'], [])) for entry in entries),
                  'real rollback removes every dedicated listener from the owned core', results)
            after_selectors = m.proxy_snapshot()
            check(all(after_selectors.get(name, {}).get('now') == selected for name, selected in before_selectors.items()),
                  'real rollback preserves original selector selection', results)
            after_controls = m.api('GET', '/configs')[1]
            check(all(after_controls.get(key) == before_controls.get(key) for key in control_keys),
                  'real rollback preserves original live main controls', results)
            check((private / 'registry.json').read_bytes() == registry_before
                  and json.loads((private / 'installed.json').read_text(encoding='utf8'))['scripts'] == {},
                  'rollback retains immutable registry reservations and clears only its installation ledger', results)
            summary['failureStage'] = 'prepare-after-rollback'
            m.prepare()
            check((private / 'registry.json').read_bytes() == registry_before,
                  'generation after rollback reuses the same reserved identities and ports', results)
            summary['dedicatedListenersVerified'] = len(entries)
            summary['passed'] = True
            summary['failureStage'] = None
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if log is not None:
                log.close()
            summary['isolatedProcessStopped'] = proc is not None and proc.poll() is not None
            summary['isolatedApiRequests'] = len(api_calls)
            summary['isolatedApiWrites'] = sum(method != 'GET' for method, _ in api_calls)
            after = next((row for row in processes(m) if row['pid'] == PROTECTED_PID), None)
            summary['productionProcessUnchanged'] = bool(after and after['path'] == protected['path'] and after['startTicks'] == protected['startTicks'])
            if not summary['productionProcessUnchanged']:
                summary['passed'] = False


def main():
    summary = {'passed': False, 'checkedAtUtc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'syntheticDataOnly': True, 'productionPrivateConfigRead': False,
               'productionApiCalls': 0, 'protectedProductionPid': PROTECTED_PID,
               'forbiddenControllerAttempts': 0, 'processDiscoveryMocked': False,
               'coreValidationMocked': False, 'apiTransportMocked': False, 'tests': []}
    try:
        execute(summary)
    except Exception as error:
        summary['passed'] = False
        summary['errorType'] = type(error).__name__
        # No traceback, YAML, response bodies, tokens or private logs in output.
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({key: summary.get(key) for key in ('passed', 'failureStage', 'errorType',
          'isolatedProcessStopped', 'productionProcessUnchanged', 'productionApiCalls',
          'isolatedApiWrites', 'dedicatedListenersVerified')}, ensure_ascii=False))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
