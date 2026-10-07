"""Portable orchestration. Public results contain names and loopback ports only."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import datetime as dt
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import urllib.error
from urllib.parse import quote, urlencode

import manager as m

VERSION = '1.0.2'

NODE_TEST_URL = 'https://www.gstatic.com/generate_204'
NODE_TEST_TIMEOUT_MS = 6000
_NODE_TEST_API_TIMEOUT = 7.5
_NON_NODE_TYPES = frozenset(('selector', 'urltest', 'fallback', 'loadbalance', 'relay',
                            'direct', 'reject', 'rejectdrop', 'reject-drop', 'pass', 'compatible', 'dns'))


class PublicError(RuntimeError):
    """Only fixed messages created by this module are safe to display."""


_operation = threading.Lock()
_PUBLIC = ('ok', 'issues', 'clashVersion', 'coreVersion', 'nodeCount', 'groupCount',
           'mode', 'dataDirectory', 'clashDirectory', 'stateDirectory', 'outputSuggested',
           'canGenerate', 'canApply', 'hasInstallation')


def public_summary(context):
    return {key: context.get(key) for key in _PUBLIC}


def _ps(script):
    prefix = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
    result = m.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', prefix + script],
                   capture_output=True, timeout=15)
    if result.returncode:
        raise PublicError('无法读取本机程序信息，请以当前 Windows 用户运行。')
    return result.stdout.decode('utf8', errors='replace').strip()


def _processes():
    raw = _ps("@(Get-Process -Name 'clash-verge','verge-mihomo' -ErrorAction SilentlyContinue | "
              "ForEach-Object {[pscustomobject]@{name=$_.ProcessName;pid=$_.Id;path=$_.Path}}) | ConvertTo-Json -Compress")
    result = json.loads(raw) if raw else []
    return result if isinstance(result, list) else [result]


def _configure(context, output=None, template=None):
    m.DATA = Path(context['dataDirectory'])
    m.PRIVATE = Path(context['stateDirectory']).resolve()
    m.CORE = Path(context['_core'])
    m.VERGE = Path(context['_verge'])
    m.POLICY_OVERRIDE = context.get('_reviewedPolicy')
    if output is not None:
        m.HERE = Path(output).resolve()
    m.TEMPLATE = Path(template).resolve() if template else None


def _fingerprint():
    _, _, script, source = m.current()
    return (source, m.digest((m.DATA / 'profiles.yaml').read_bytes()),
            m.digest((m.DATA / 'clash-verge.yaml').read_bytes()), m.digest(script.read_bytes()))


def _real_nodes(base):
    nodes = []
    names = set()
    for node in base.get('proxies', []):
        name = node.get('name')
        if not isinstance(name, str) or not name or name in names:
            raise PublicError('存在空节点名或重名节点，无法安全建立一对一映射。')
        names.add(name)
        if node.get('type') in ('direct', 'reject', 'reject-drop', 'pass', 'compatible'):
            continue
        if re.fullmatch(r'\*.*官网.*\*', name) or any(s in name for s in ('剩余流量', '套餐到期', '订阅更新', '到期时间')):
            continue
        if node.get('dialer-proxy'):
            raise PublicError('含链式代理节点，当前版本需要单独核验此配置。')
        nodes.append(node)
    return nodes


def _leaf_actions(name, proxies, visiting=None):
    if name in ('DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'):
        return {name}
    visiting = set() if visiting is None else set(visiting)
    if name in visiting or name not in proxies:
        raise PublicError('代理组有循环或无法解析的引用，已停止。')
    visiting.add(name)
    item = proxies[name]
    if item.get('type') in ('Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Relay'):
        all_names = item.get('all', [])
        if not all_names:
            raise PublicError('代理组没有可核验的成员，已停止。')
        return set().union(*(_leaf_actions(n, proxies, visiting) for n in all_names))
    return {'PROXY'}


def detect(data_directory=None, clash_directory=None, state_directory=None):
    """Read only: no mkdir, backup, registry write, browser access or controller PUT."""
    with _operation:
        data = Path(data_directory) if data_directory else Path(os.environ['APPDATA']) / 'io.github.clash-verge-rev.clash-verge-rev'
        state = Path(state_directory) if state_directory else Path(os.environ['LOCALAPPDATA']) / 'CodexClashZeroOmega'
        context = dict(ok=False, issues=[], clashVersion='未检测', coreVersion='未检测', nodeCount=0,
                       groupCount=0, mode='未检测', dataDirectory=str(data.resolve()),
                       clashDirectory=str(Path(clash_directory).resolve()) if clash_directory else '',
                       stateDirectory=str(state.resolve()),
                       outputSuggested=str(Path.home() / 'Documents' / 'ClashZeroOmega'),
                       canGenerate=False, canApply=False, hasInstallation=False)
        try:
            processes = _processes()
            verge_paths = {str(Path(p['path']).resolve()) for p in processes
                           if p['name'] == 'clash-verge' and p.get('path')}
            if clash_directory:
                verge = Path(clash_directory).resolve() / 'clash-verge.exe'
                if str(verge) not in verge_paths:
                    raise PublicError('指定目录的 Clash Verge Rev 未运行，请先打开它并启用订阅。')
            elif len(verge_paths) == 1:
                verge = Path(next(iter(verge_paths)))
            else:
                raise PublicError('未找到唯一运行中的 Clash Verge Rev，请先打开它；多实例时选择程序目录。')
            core = verge.parent / 'verge-mihomo.exe'
            core_processes = [p for p in processes if p['name'] == 'verge-mihomo' and p.get('path')
                              and Path(p['path']).resolve() == core.resolve()]
            if len(core_processes) != 1 or not core.is_file():
                raise PublicError('未找到对应的唯一 Mihomo 核心，请先在 Clash 中启用订阅。')
            context.update(clashDirectory=str(verge.parent), _verge=str(verge), _core=str(core),
                           _corePid=core_processes[0]['pid'])
            _configure(context)
            for name in ('clash-verge.yaml', 'profiles.yaml'):
                if not (m.DATA / name).is_file():
                    raise PublicError('Clash 数据目录不完整，可在高级设置中选择正确目录。')
            context['clashVersion'] = _ps("(Get-Item -LiteralPath '" + str(verge).replace("'", "''") + "').VersionInfo.ProductVersion")
            version_text = m.run([str(core), '-v'], capture_output=True, timeout=10).stdout.decode(errors='replace')
            match = re.search(r'Mihomo Meta v([^ ]+)', version_text)
            context['coreVersion'] = match[1] if match else '未知'
            if context['clashVersion'] != '2.5.7' or context['coreVersion'] != '1.19.32':
                raise PublicError('当前仅验证 Clash Verge Rev 2.5.7 + Mihomo 1.19.32；此版本组合暂不开放生成或应用。')
            runtime = m.read_yaml(m.DATA / 'clash-verge.yaml')
            live = m.api('GET', '/configs', config=runtime)[1]
            live_version = m.api('GET', '/version', config=runtime)[1].get('version', '').lstrip('v')
            if live_version != context['coreVersion']:
                raise PublicError('控制接口与已检测核心版本不一致，已停止。')
            context['mode'] = live.get('mode', '未知')
            if runtime.get('mode') != 'rule' or live.get('mode') != 'rule':
                raise PublicError('请先在 Clash 中选择规则模式。软件不会自动修改模式。')
            # Verify the controller belongs to the selected data/core instance.
            port = live.get('mixed-port') or live.get('port') or live.get('socks-port')
            if not port or not any(pid == context['_corePid'] for _, pid in m.listener_owners().get(port, [])):
                raise PublicError('无法确认控制接口与当前核心的对应关系，已停止。')
            base = m.unmanaged(runtime)
            if base.get('proxy-providers') or base.get('rule-providers'):
                raise PublicError('当前版本仅支持内嵌节点和内嵌规则；provider 配置需要单独核验。')
            if base.get('sub-rules'):
                raise PublicError('已有自定义子规则需要单独核验，软件将保留原配置。')
            if any('REGEX' in rule.upper() for rule in base.get('rules', []) if isinstance(rule, str)):
                raise PublicError('正则规则需要单独核验，已停止。')
            nodes = _real_nodes(base)
            context['nodeCount'] = len(nodes)
            context['groupCount'] = len(base.get('proxy-groups', []))
            if not nodes:
                raise PublicError('当前配置没有可用的真实节点定义，请先更新订阅。')
            proxies = m.proxy_snapshot(runtime)
            if any(n['name'] not in proxies for n in nodes):
                raise PublicError('配置文件与当前核心节点不一致，请先让 Clash 完成更新后重新探测。')
            _, _, script, source = m.current()
            registry_path = m.PRIVATE / 'registry.json'
            registry = json.loads(registry_path.read_text(encoding='utf8')) if registry_path.exists() else {}
            cached = registry.get('policies', {}).get(source, {})
            policy_base, review = {}, []
            for group in base.get('proxy-groups', []):
                actions = _leaf_actions(group['name'], proxies)
                if cached.get(group['name']) in actions:
                    policy_base[group['name']] = cached[group['name']]
                elif len(actions) == 1:
                    policy_base[group['name']] = next(iter(actions))
                else:
                    review.append({'name': group['name'], 'choices': sorted(actions),
                                   'currentAction': m.resolve_action(group['name'], proxies)})
            context.update(groupPolicies=review, needsPolicyReview=bool(review), _policyBase=policy_base)
            installed_path = m.PRIVATE / 'installed.json'
            installed = json.loads(installed_path.read_text(encoding='utf8')) if installed_path.exists() else {}
            context['hasInstallation'] = bool(installed.get('scripts'))
            recovery_path = m.PRIVATE / 'recovery.json'
            if recovery_path.exists() and json.loads(recovery_path.read_text(encoding='utf8')).get('phase') in ('applying', 'rolling-back', 'recovery-required'):
                raise PublicError('检测到尚未完成的恢复记录，请先核验私有备份；已禁止进一步应用。')
            original = script.read_text(encoding='utf8')
            if original.startswith(m.MARKER):
                record = installed.get('scripts', {}).get(str(script))
                if not record or m.digest(script.read_bytes()) != record['hash'] or not Path(record['original']).is_file():
                    raise PublicError('检测到已管理扩展，但缺少匹配的私有状态或扩展有外部改动；请在高级设置选择原私有状态目录。')
                original = Path(record['original']).read_text(encoding='utf8')
            plain = re.sub(r'/\*.*?\*/|//[^\n]*', '', original, flags=re.S)
            if not re.fullmatch(r'\s*function\s+main\s*\(\s*config\s*,\s*profileName\s*\)\s*\{\s*return\s+config\s*;?\s*\}\s*', plain):
                raise PublicError('当前订阅扩展含自定义逻辑，需要先核验完整扩展流程，软件不会覆盖它。')
            context.update(ok=True, canGenerate=True, canApply=True, _fingerprint=_fingerprint())
        except Exception as error:
            context['issues'].append(safe_error(error))
        return context


def safe_error(error):
    """Never expose YAML, subprocess output, API bodies or arbitrary exception text."""
    if isinstance(error, PublicError):
        return str(error)
    backend_messages = {
        'Active subscription has no dedicated script; stopped': '当前订阅未关联独立扩展脚本，需要先在 Clash 中建立关联。',
        'Local controller pipe unavailable': '本机控制接口不可用，请确认 Clash 核心正在运行。',
        'No supported local controller': '没有可用的本机控制接口，未添加新的控制端口。',
        'Port registry conflict; ports were not reassigned': '已保留的端口发生冲突，软件没有重新分配旧端口。',
        'Reserved registry port is occupied by another process; stopped': '旧端口被其他程序占用，应用已停止，映射保留。',
        'Routing transform rejected this configuration': '规则转换未通过，当前配置需要单独核验。',
        'Mihomo final configuration validation failed; private diagnostics retained': 'Mihomo 校验未通过，原配置未应用，诊断仅保留在私有目录。',
        'Managed script changed externally; stopped to preserve edits': '扩展存在外部改动，已停止以保留原文件。',
        'Extension has external edits; rollback stopped': '扩展存在外部改动，回滚已停止以保留原文件。',
        'Original script changed; backup review required': '原扩展发生变化，需要核验备份后继续。',
        'Application failed and recovery is incomplete; private recovery manifest retained': '应用失败且恢复未完成，请核验私有恢复记录后再操作。',
        'Rollback recovery is incomplete; private recovery manifest retained': '回滚失败且恢复未完成，请核验私有恢复记录后再操作。',
        'Configuration changed since validation; run update again': '配置已经变化，请重新探测和生成。',
        'Live main routing controls changed since validation; stopped without applying': '电脑主路由设置已经变化，请重新探测和生成。',
    }
    if isinstance(error, RuntimeError) and str(error) in backend_messages:
        return backend_messages[str(error)]
    return '操作未完成（' + type(error).__name__ + '）。未输出私密配置，请重新探测或核验私有备份。'


def review_policies(context, choices):
    expected = {row['name']: row['choices'] for row in context.get('groupPolicies', [])}
    if set(choices) != set(expected) or any(value not in expected[name] for name, value in choices.items()):
        raise PublicError('请为每个有冲突的代理组明确选择分流动作。')
    context['_reviewedPolicy'] = {**context.get('_policyBase', {}), **choices}
    context['needsPolicyReview'] = False


def _ensure_private():
    known = {'registry.json', 'installed.json', 'prepared.json', 'recovery.json',
             'original-scripts', 'backups', 'staging', 'rollback', 'operation.lock',
             'isolated-core-tests', 'mapping-tests', 'transaction-tests', 'initial-backup.txt'}
    if (m.PRIVATE == Path(m.PRIVATE.anchor) or m.PRIVATE == Path.home().resolve()
        or m.PRIVATE == m.DATA or m.PRIVATE == m.RESOURCES or m.DATA in m.PRIVATE.parents):
        raise PublicError('请为私有状态选择独立的专用目录。')
    if m.PRIVATE.exists() and any(p.name not in known for p in m.PRIVATE.iterdir()):
        raise PublicError('私有状态目录含非本工具文件，请选择空目录或原来的专用状态目录。')
    m.PRIVATE.mkdir(parents=True, exist_ok=True)
    sid = _ps('[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value')
    if not re.fullmatch(r'S-1-\d+(?:-\d+)+', sid):
        raise PublicError('无法确认当前用户的私有备份权限，已停止。')
    path = str(m.PRIVATE).replace("'", "''")
    acl = ("$sid=New-Object System.Security.Principal.SecurityIdentifier('" + sid + "'); "
           "$acl=New-Object System.Security.AccessControl.DirectorySecurity; "
           "$acl.SetAccessRuleProtection($true,$false); "
           "foreach($s in @($sid,(New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')))){"
           "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($s,'FullControl','ContainerInherit,ObjectInherit','None','Allow');$acl.AddAccessRule($rule)}; "
           "Set-Acl -LiteralPath '" + path + "' -AclObject $acl -ErrorAction Stop")
    _ps(acl)


@contextmanager
def _private_lock():
    with open(m.PRIVATE / 'operation.lock', 'a+b') as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise PublicError('另一窗口正在生成、应用或回滚，请等它完成。') from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _check(context):
    if not context.get('ok') or not context.get('canGenerate'):
        raise PublicError('请先完成环境探测。')
    _configure(context)
    if _fingerprint() != context.get('_fingerprint'):
        raise PublicError('订阅或配置已变化，请重新探测后生成。')


def _checked_leaf_types(nodes, proxies):
    """Reject groups and aliases before any outbound connectivity test."""
    result = {}
    for node in nodes:
        name = node['name']
        live = proxies.get(name)
        kind = live.get('type', '').lower() if isinstance(live, dict) else ''
        if (not kind or kind in _NON_NODE_TYPES or 'all' in live
            or live.get('name', name) != name or live.get('dialer-proxy')):
            raise PublicError('节点与核心真实线路不一致，请等 Clash 更新完成后重新探测。')
        result[name] = kind
    return result


def _node_timeout(error):
    # HTTP/named-pipe errors are classified without exposing their body or URL.
    return (isinstance(error, TimeoutError)
            or isinstance(error, urllib.error.HTTPError) and error.code in (408, 504)
            or isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
            or type(error) is RuntimeError and str(error) in (
                'Local API failed: HTTP 408', 'Local API failed: HTTP 504'))


def check_nodes(context, on_progress=None):
    """One named-leaf HEAD test per node; no selector changes or config writes.

    Results are a snapshot of HTTPS reachability to NODE_TEST_URL. Mihomo updates
    its delay history. Its existing unified-delay setting may cause a second HEAD
    inside one URLTest; this tool leaves that setting untouched.
    """
    with _operation:
        context.pop('_nodeChecks', None)
        _check(context)
        if m.core_process_ids() != {context['_corePid']}:
            raise PublicError('运行核心已经变化，请重新探测。')
        runtime = m.read_yaml(m.DATA / 'clash-verge.yaml')
        nodes = _real_nodes(m.unmanaged(runtime))
        if not nodes or len(nodes) != context.get('nodeCount'):
            raise PublicError('当前节点列表已经变化，请重新探测。')
        before = m.proxy_snapshot(runtime)
        leaf_types = _checked_leaf_types(nodes, before)
        selectors = {name: value.get('now') for name, value in before.items()
                     if value.get('type') == 'Selector'}
        live = m.api('GET', '/configs', config=runtime)[1]
        main_port = live.get('mixed-port') or live.get('port') or live.get('socks-port')
        if (live.get('mode') != 'rule' or not main_port
            or not any(pid == context['_corePid'] for _, pid in m.listener_owners().get(main_port, []))):
            raise PublicError('无法确认当前规则模式和核心入口，请重新探测。')
        query = urlencode({'url': NODE_TEST_URL, 'timeout': NODE_TEST_TIMEOUT_MS, 'expected': '204'})

        def test_one(name):
            if _fingerprint() != context['_fingerprint']:
                raise PublicError('测试期间订阅或配置已变化，请重新探测。')
            result = {'nodeName': name, 'status': 'failed', 'delayMs': None}
            path = '/proxies/' + quote(name, safe='')
            try:
                status, body = m.api('GET', path + '/delay?' + query, config=runtime,
                                     timeout=_NODE_TEST_API_TIMEOUT)
                if status in (408, 504):
                    result['status'] = 'timeout'
                elif status == 200 and isinstance(body, dict):
                    delay = body.get('delay')
                    if isinstance(delay, int) and not isinstance(delay, bool) and 0 <= delay <= 65535:
                        # v1.19.32 records expected-status failures in extra.alive;
                        # the delay endpoint alone can return 200 for a wrong status.
                        metadata_status, metadata = m.api('GET', path, config=runtime,
                                                         timeout=_NODE_TEST_API_TIMEOUT)
                        if metadata_status == 200 and isinstance(metadata, dict):
                            _checked_leaf_types([{'name': name}], {name: metadata})
                            tested = metadata.get('extra', {}).get(NODE_TEST_URL, {})
                            if tested.get('alive') is True:
                                result.update(status='reachable', delayMs=delay)
            except PublicError:
                raise
            except Exception as error:
                result['status'] = 'timeout' if _node_timeout(error) else 'failed'
            return result

        checked = {}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='czo-node-check') as executor:
            futures = {executor.submit(test_one, node['name']): node['name'] for node in nodes}
            try:
                for future in as_completed(futures):
                    result = future.result()
                    checked[result['nodeName']] = result
                    if on_progress is not None:
                        on_progress(dict(result))
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        if _fingerprint() != context['_fingerprint'] or m.core_process_ids() != {context['_corePid']}:
            raise PublicError('测试期间订阅、配置或核心已变化，请重新探测。')
        after = m.proxy_snapshot(runtime)
        if _checked_leaf_types(nodes, after) != leaf_types:
            raise PublicError('测试期间核心节点列表已变化，请重新探测。')
        if m.api('GET', '/configs', config=runtime)[1] != live:
            raise PublicError('测试期间核心设置已变化，请重新探测。')
        if selectors != {name: value.get('now') for name, value in after.items()
                         if value.get('type') == 'Selector'}:
            raise PublicError('测试期间电脑默认线路发生变化，请重新探测。')
        result = {'checks': [checked[node['name']] for node in nodes],
                  'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                  'total': len(nodes)}
        context['_nodeChecks'] = copy.deepcopy(result)
        return result


def _template_check(path):
    if not path:
        return
    options = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(options, dict) or options.get('schemaVersion') != 2:
        raise PublicError('模板必须是 ZeroOmega 原生 schemaVersion 2 备份。')
    from zeroomega import _DEFAULT_OPTIONS, _PROFILE_FIELDS, _BYPASS_LIST
    for key, profile in options.items():
        if key.startswith('+'):
            proxy = profile.get('fallbackProxy', {}) if isinstance(profile, dict) else {}
            if (not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS
                or profile.get('profileType') != 'FixedProfile' or set(proxy) != {'scheme', 'host', 'port'}
                or proxy.get('scheme') != 'socks5' or proxy.get('host') not in ('127.0.0.1', 'localhost', '::1', '[::1]')
                or profile.get('bypassList') != _BYPASS_LIST or profile.get('proxyDNS') is not True):
                raise PublicError('模板含非本地 SOCKS5 情景或其他规则，请选择纯本地原生模板，或留空使用内置格式。')
        elif key not in _DEFAULT_OPTIONS:
            raise PublicError('模板含未支持的额外字段，已停止；可留空使用内置原生格式。')
    if any('password' in str(k).lower() or 'token' in str(k).lower() or 'auth' in str(k).lower() for k in options):
        raise PublicError('模板含凭据字段，已停止生成。')


def generate(context, output_directory, template_path=None):
    with _operation:
        _check(context)
        if context.get('needsPolicyReview'):
            raise PublicError('需要先确认混合代理组的分流动作，界面会列出需要选择的组。')
        context.setdefault('_reviewedPolicy', context.get('_policyBase'))
        _template_check(template_path)
        output = Path(output_directory).resolve()
        if (output == m.DATA or output == m.RESOURCES or output == m.PRIVATE
            or m.DATA in output.parents or m.RESOURCES in output.parents
            or m.PRIVATE in output.parents or output in m.PRIVATE.parents):
            raise PublicError('请把成品保存到独立导出目录。')
        _configure(context, output, template_path)
        _ensure_private()
        with _private_lock():
            output.mkdir(parents=True, exist_ok=True)
            _, _, script, _ = m.current()
            # Back up before staging or allocating any permanent mapping.
            paths = [script] + [m.DATA / x for x in ('clash-verge.yaml', 'profiles.yaml', 'config.yaml', 'verge.yaml', 'dns_config.yaml')]
            paths += list((m.DATA / 'profiles').glob('*.js')) + list((m.DATA / 'profiles').glob('*.yaml'))
            backup = m.private_backup(list(dict.fromkeys(paths)))
            for folder, candidates in (
                ('exports', [output / name for name in (m.BAK, 'node-port-map.json', '节点与端口映射表.md', '待同步的订阅扩展.js', '验证摘要.json')]),
                ('private-state', [m.PRIVATE / name for name in ('registry.json', 'installed.json', 'prepared.json')]),
            ):
                for path in candidates:
                    if path.is_file():
                        target = backup / folder / path.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
            prepared = m.prepare()
            if _fingerprint() != context['_fingerprint']:
                raise PublicError('生成期间 Clash 配置发生变化，请重新探测。')
            summary = json.loads((output / '验证摘要.json').read_text(encoding='utf8'))
            entries = json.loads((output / 'node-port-map.json').read_text(encoding='utf8'))
            owners = m.listener_owners()
            healthy = all(owners.get(e['port']) == [('127.0.0.1:' + str(e['port']), context['_corePid'])] for e in entries)
            result = dict(bakPath=str(output / m.BAK), mapPath=str(output / '节点与端口映射表.md'),
                          extensionPath=str(script),
                          entries=entries, needsApply=summary['status'] != 'installed-and-live' or not healthy,
                          backupPath=str(backup), **{k: prepared['changes'][k] for k in ('new', 'active', 'inactive')})
            context.update(_prepared=prepared, _output=str(output), _template=template_path, _generated=result)
            return result


def apply(context):
    with _operation:
        _check(context)
        if not context.get('_prepared') or not context.get('_generated', {}).get('needsApply'):
            raise PublicError('请先生成需要应用的新配置。')
        _configure(context, context['_output'], context.get('_template'))
        _ensure_private()
        with _private_lock():
            result = m.install(context['_prepared'])
        context.update(hasInstallation=True, _fingerprint=_fingerprint())
        context['_generated']['needsApply'] = False
        return result


def rollback(context):
    with _operation:
        _check(context)
        if not context.get('hasInstallation'):
            raise PublicError('未找到可回滚的已安装扩展。')
        _configure(context, context.get('_output') or context['outputSuggested'])
        _ensure_private()
        with _private_lock():
            result = m.rollback()
        context.update(hasInstallation=False, canGenerate=False, canApply=False, _fingerprint=_fingerprint())
        return result
