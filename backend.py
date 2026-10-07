"""Private multi-subscription browser core, independent of Clash Verge's selection.

The public Backend methods return only safe summaries. Subscription contents,
controller secrets, source URLs and generated runtime configurations remain in
the protected private directory. No method writes to Clash or browser storage.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

import yaml

import bridge_lifecycle as life
from clash_api import api
from zeroomega import build_backup

KIND = "codex-multi-subscription-hub"
SCHEMA = 1
FIRST_PORT = 46000
NS = "msh.v1."
BAK_NAME = "ZeroOmega-多订阅-手动选择-独立分流.bak"
ACTIONS = {"DIRECT", "REJECT", "REJECT-DROP", "PROXY"}
BUILTIN_ACTIONS = {"DIRECT", "REJECT", "REJECT-DROP"}
SUPPORTED_TYPES = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic", "socks5", "http", "snell", "wireguard", "mieru", "anytls", "ssh"}
AUTH_FIELDS = {"password", "uuid", "uid", "username", "token", "private-key", "pre-shared-key", "public-key", "client-id", "alterId", "auth", "auth-str", "certificate", "private-key-file"}
ORDINARY_RULES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "IP-CIDR6", "GEOIP", "GEOSITE", "IP-ASN", "SRC-IP-CIDR", "SRC-PORT", "DST-PORT", "PROCESS-NAME", "PROCESS-PATH", "NETWORK", "IN-NAME", "IN-TYPE", "RULE-SET", "UID", "DSCP"}
COMPLEX_RULES = {"AND", "OR", "NOT"}


class HubError(life.BridgeError):
    """A non-sensitive stable code, safe for the GUI and public reports."""


def require(condition, code):
    if not condition:
        raise HubError(code)


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(raw).hexdigest()


def safe_display_name(value, fallback="未命名"):
    name = str(value or fallback)
    # User-controlled labels may themselves contain a subscription/connection
    # URI. Keep labels useful without exposing embedded authentication tokens.
    if re.search(r"(?i)(?:https?|ss|ssr|vmess|vless|trojan|hysteria2?|tuic|socks5?)://", name):
        return fallback + " · " + digest(name)[:8]
    return name


def source_identity(item):
    """A source cannot inherit another subscription's old ports after URL changes."""
    require(isinstance(item, dict) and isinstance(item.get("uid"), str) and item["uid"], "SOURCE_INVALID")
    return digest([item["uid"], item.get("url", ""), item.get("type", "")])


def node_identity(source_id, node):
    """Keep accounts/password rotations stable; endpoint changes get new ports.

    Account keys are not route identifiers. Identical endpoint aliases are
    disambiguated by assign_entries, so two aliases are never silently merged.
    """
    require(isinstance(node, dict), "NODE_INVALID")
    shape = {key: value for key, value in node.items() if key not in AUTH_FIELDS | {"name"}}
    return digest([source_id, shape])


def valid_node(node):
    if not isinstance(node, dict) or node.get("type") not in SUPPORTED_TYPES:
        return False
    name = node.get("name")
    return (isinstance(name, str) and bool(name.strip()) and not any(ord(c) < 32 for c in name)
            and isinstance(node.get("server"), str) and bool(node["server"])
            and type(node.get("port")) is int and 1 <= node["port"] <= 65535)


def assign_entries(registry, sources, port_usable=None):
    """Pure stable allocation. Old ports/names remain allocated, even when dead."""
    require(isinstance(registry, dict) and registry.get("schema", SCHEMA) == SCHEMA, "REGISTRY_INVALID")
    old = registry.get("entries", [])
    require(isinstance(old, list), "REGISTRY_INVALID")
    entries = copy.deepcopy(old)
    ports, ids, internal_names, listener_names = set(), set(), set(), set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["id"])
                and type(entry.get("port")) is int and FIRST_PORT <= entry["port"] <= 65535
                and entry["id"] not in ids and entry["port"] not in ports, "REGISTRY_INVALID")
        require(entry.get("internalName") == NS + "node." + entry["id"][:32]
                and isinstance(entry.get("sourceId"), str) and re.fullmatch(r"[0-9a-f]{64}", entry["sourceId"])
                and isinstance(entry.get("sourceName"), str) and bool(entry["sourceName"])
                and isinstance(entry.get("nodeName"), str) and bool(entry["nodeName"])
                and isinstance(entry.get("profileName"), str) and bool(entry["profileName"])
                and safe_display_name(entry["profileName"]) == entry["profileName"]
                and type(entry.get("active")) is bool, "REGISTRY_INVALID")
        require(entry["internalName"] not in internal_names and entry["id"][:24] not in listener_names, "REGISTRY_IDENTITY_COLLISION")
        ids.add(entry["id"]); ports.add(entry["port"])
        internal_names.add(entry["internalName"]); listener_names.add(entry["id"][:24])
        entry["active"] = False
    by_id = {entry["id"]: entry for entry in entries}
    next_port = max([FIRST_PORT - 1, *ports]) + 1
    selected_ids = set()
    for source in sources:
        require(isinstance(source, dict) and isinstance(source.get("id"), str)
                and source["id"] not in selected_ids and isinstance(source.get("nodes"), list), "SOURCE_INVALID")
        selected_ids.add(source["id"])
        require(all(valid_node(node) for node in source["nodes"]), "NODE_INVALID")
        names = [node["name"] for node in source["nodes"]]
        require(len(set(names)) == len(names), "DUPLICATE_NODE_NAME")
        fingerprints = Counter(node_identity(source["id"], node) for node in source["nodes"])
        # Sort only *new* allocations. Never use subscription order as identity.
        for node in sorted(source["nodes"], key=lambda n: (n["name"], node_identity(source["id"], n))):
            identity = node_identity(source["id"], node)
            if fingerprints[identity] > 1:
                identity = digest([identity, node["name"]])
            entry = by_id.get(identity)
            if entry is None:
                while next_port <= 65535 and port_usable is not None and not port_usable(next_port):
                    next_port += 1
                require(next_port <= 65535, "PORT_RANGE_EXHAUSTED")
                entry = {"id": identity, "sourceId": source["id"], "sourceName": source["name"],
                         "nodeName": node["name"], "profileName": "", "internalName": NS + "node." + identity[:32],
                         "port": next_port, "active": True}
                require(entry["internalName"] not in internal_names and identity[:24] not in listener_names, "REGISTRY_IDENTITY_COLLISION")
                internal_names.add(entry["internalName"]); listener_names.add(identity[:24])
                entries.append(entry); by_id[identity] = entry; next_port += 1
            entry.update({"active": True, "sourceName": source["name"], "nodeName": node["name"]})

    used_names = {entry.get("profileName") for entry in entries if entry.get("profileName")}
    require(len(used_names) == sum(bool(entry.get("profileName")) for entry in entries), "REGISTRY_NAME_COLLISION")
    active_name_counts = Counter(entry["nodeName"] for entry in entries if entry["active"])
    for entry in entries:
        if entry.get("profileName"):
            continue
        name = safe_display_name(entry["nodeName"].strip(), "节点")
        if name in {"direct", "system", "auto_detect"} or name.startswith("_"):
            name = "节点 · " + name
        if active_name_counts[entry["nodeName"]] > 1 or name in used_names:
            name += " · " + entry["sourceName"]
        candidate = name
        if candidate in used_names:
            candidate += " · " + entry["id"][:8]
        require(candidate not in used_names, "PROFILE_NAME_COLLISION")
        entry["profileName"] = candidate; used_names.add(candidate)
    return sorted(entries, key=lambda entry: entry["port"])


def split_rule(text):
    require(isinstance(text, str), "RULE_INVALID")
    depth, start, pieces = 0, 0, []
    for index, char in enumerate(text):
        if char == "(": depth += 1
        elif char == ")": depth -= 1
        require(depth >= 0, "RULE_INVALID")
        if char == "," and depth == 0:
            pieces.append(text[start:index].strip()); start = index + 1
    require(depth == 0, "RULE_INVALID")
    pieces.append(text[start:].strip())
    return pieces


def rule_target(text):
    pieces = split_rule(text)
    kind = pieces[0]
    if kind == "SUB-RULE":
        require(len(pieces) == 3, "RULE_INVALID")
        return None
    if kind == "MATCH":
        require(len(pieces) == 2, "RULE_INVALID")
        return pieces[1]
    require(kind in ORDINARY_RULES | COMPLEX_RULES and len(pieces) >= 3, "RULE_SYNTAX_REVIEW_REQUIRED")
    return pieces[2]


def assess_policy(base, reviewed=None):
    """Classify topology only when every branch agrees; never guess selectors."""
    require(isinstance(base, dict), "RULE_CONFIG_INVALID")
    reviewed = reviewed or {}
    require(isinstance(reviewed, dict) and all(value in ACTIONS for value in reviewed.values()), "POLICY_INVALID")
    nodes = {n["name"] for n in base.get("proxies", []) if isinstance(n, dict) and isinstance(n.get("name"), str)}
    groups = {g["name"]: g for g in base.get("proxy-groups", []) if isinstance(g, dict) and isinstance(g.get("name"), str) and not g["name"].startswith(("czo.v1.", NS))}

    def categories(name, visited=None):
        if name in BUILTIN_ACTIONS: return {name}
        if name in nodes: return {"PROXY"}
        visited = set() if visited is None else set(visited)
        if name in visited or name not in groups: return {"UNKNOWN"}
        visited.add(name)
        group = groups[name]
        result = set()
        for member in group.get("proxies", []):
            result |= categories(member, visited)
        if group.get("use") or group.get("include-all") or group.get("include-all-proxies") or group.get("include-all-providers"):
            result.add("PROXY")
        return result or {"UNKNOWN"}

    targets = set()
    for rules in [base.get("rules", []), *base.get("sub-rules", {}).values()]:
        if not isinstance(rules, list): continue
        for rule in rules:
            target = rule_target(rule)
            if target is not None: targets.add(target)
    policy, issues = {}, []
    for target in sorted(targets):
        if target in BUILTIN_ACTIONS:
            policy[target] = target
        elif target in reviewed:
            policy[target] = reviewed[target]
        else:
            found = categories(target)
            if len(found) == 1 and "UNKNOWN" not in found:
                policy[target] = next(iter(found))
            else:
                require(safe_display_name(target) == target, "POLICY_TARGET_LABEL_REQUIRES_REVIEW")
                issues.append({"target": target, "reason": "AMBIGUOUS_GROUP_POLICY", "categories": sorted(found)})
    return policy, issues


def clean_routing(base):
    result = copy.deepcopy(base)
    result["proxy-groups"] = [g for g in result.get("proxy-groups", []) if not str(g.get("name", "")).startswith(("czo.v1.", NS))]
    result["sub-rules"] = {key: value for key, value in result.get("sub-rules", {}).items() if not key.startswith(("czo.v1.", NS))}
    return result


def convert_routing(base, entries, policy):
    """Route a matching proxy request to exactly its IN-NAME-bound real node."""
    require(isinstance(policy, dict) and all(value in ACTIONS for value in policy.values()), "POLICY_INVALID")
    require(isinstance(entries, list), "REGISTRY_INVALID")
    result = copy.deepcopy(base)
    # Original proxy groups are never carried into the independent core.
    result["proxy-groups"] = []
    result["listeners"] = []
    result["sub-rules"] = {}
    available = {node["name"] for node in result.get("proxies", [])}
    dispatch = []
    ports, ids = set(), set()
    for entry in entries:
        require(type(entry.get("port")) is int and FIRST_PORT <= entry["port"] <= 65535
                and entry["port"] not in ports and entry.get("id") not in ids, "REGISTRY_INVALID")
        ports.add(entry["port"]); ids.add(entry["id"])
        alive = bool(entry.get("active")) and entry.get("internalName") in available
        listener = NS + "in." + entry["id"][:24]
        result["listeners"].append({"name": listener, "type": "socks", "listen": "127.0.0.1", "port": entry["port"],
                                    "udp": False, "rule": NS + ("rules" if alive else "dead")})
        dispatch.append("IN-NAME," + listener + "," + (entry["internalName"] if alive else "REJECT"))
    dispatch.append("MATCH,REJECT")
    subs = result["sub-rules"]
    subs[NS + "dispatch"] = dispatch
    subs[NS + "dead"] = ["MATCH,REJECT"]
    original_subs = base.get("sub-rules", {})
    seen, serial = set(), [0]

    def convert(rules):
        require(isinstance(rules, list), "RULE_INVALID")
        converted = []
        for text in rules:
            pieces = split_rule(text); kind = pieces[0]
            if kind == "SUB-RULE":
                require(len(pieces) == 3 and pieces[2] in original_subs and pieces[2] not in seen, "SUBRULE_INVALID")
                serial[0] += 1; name = NS + "nested." + str(serial[0])
                seen.add(pieces[2]); subs[name] = convert(original_subs[pieces[2]]); seen.remove(pieces[2])
                converted.append("SUB-RULE," + pieces[1] + "," + name)
                continue
            target = rule_target(text)
            action = target if target in BUILTIN_ACTIONS else policy.get(target)
            require(action in ACTIONS, "POLICY_REVIEW_REQUIRED")
            # User-approved requirement: unmatched traffic uses this browser's
            # fixed proxy, preserving MATCH block actions when explicitly set.
            if kind == "MATCH" and action not in {"REJECT", "REJECT-DROP"}: action = "PROXY"
            target_index = 1 if kind == "MATCH" else 2
            if action != "PROXY":
                pieces[target_index] = action
                converted.append(",".join(pieces))
            else:
                predicate = "OR,((NETWORK,tcp),(NETWORK,udp))" if kind == "MATCH" else ",".join(piece for index, piece in enumerate(pieces) if index != target_index)
                converted.append("SUB-RULE,(" + predicate + ")," + NS + "dispatch")
                pieces[target_index] = "REJECT"; converted.append(",".join(pieces))
        converted.append("MATCH,REJECT")
        return converted

    subs[NS + "rules"] = convert(base.get("rules", []))
    result["rules"] = ["MATCH,REJECT"]
    return result


def safe_public(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except life.BridgeError as error:
            return {"ok": False, "code": error.code}
        except Exception:
            return {"ok": False, "code": "LOCAL_OPERATION_FAILED"}
    return wrapped


def _atomic_yaml(path, value):
    path = path.parent.resolve() / path.name
    temporary = life.safe_child(path.parent, path.name + ".tmp")
    temporary.write_bytes(life.yaml_bytes(value))
    os.replace(temporary, path)


def _read_yaml(path):
    value = life.read_yaml(path)
    require(isinstance(value, dict), "CONFIG_INVALID")
    return value


def _port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                handle.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            handle.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def excluded_ports():
    if os.name != "nt": return set()
    result = set()
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/netsh.exe"
    for family in ("ipv4", "ipv6"):
        with life._child_dll_search():
            process = subprocess.run([str(executable), "interface", family, "show", "excludedportrange", "protocol=tcp"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        require(process.returncode == 0, "EXCLUDED_PORT_QUERY_FAILED")
        for line in process.stdout.decode(errors="replace").splitlines():
            match = re.fullmatch(r"\s*(\d+)\s+(\d+)\s*\*?\s*", line)
            if match: result.update(range(int(match[1]), int(match[2]) + 1))
    return result


class Backend:
    def __init__(self, root: Path, clash_dir=None, core_path=None, bridge_root=None):
        self.root = Path(root).absolute().resolve()
        self.clash_dir = Path(clash_dir or (Path(os.environ.get("APPDATA", "")) / "io.github.clash-verge-rev.clash-verge-rev")).absolute().resolve()
        self.core_path = Path(core_path).absolute().resolve() if core_path else None
        self.bridge_root = Path(bridge_root or r"G:\谷歌下载\插件--管理\飞鸟-Clash桥接\private").absolute().resolve()
        self._sources = {}
        self._base = None
        self._current = None
        self._last_prepared = None

    def _path(self, relative, must_exist=False):
        return life.safe_child(self.root, relative, must_exist=must_exist)

    def _ensure_root(self):
        if not self.root.exists():
            life.safe_child(self.root.parent, self.root.name)
            self.root.mkdir(parents=True)
            life.protect_directory(self.root)
            life.verify_acl(self.root)
            for relative in ("bin", "data", "prepared", "backups", "logs"):
                self._path(relative).mkdir()
            life.atomic_json(self._path("manifest.json"), {"kind": KIND, "schema": SCHEMA, "installId": uuid.uuid4().hex,
                                                            "controllerSecret": secrets.token_urlsafe(36)})
            life.atomic_json(self._path("registry.json"), {"schema": SCHEMA, "entries": [], "policy": {}})
        manifest = life.read_json(self._path("manifest.json", True))
        require(manifest.get("kind") == KIND and manifest.get("schema") == SCHEMA, "UNMANAGED_DIRECTORY")
        require(isinstance(manifest.get("controllerSecret"), str) and len(manifest["controllerSecret"]) >= 24, "MANIFEST_INVALID")
        life.verify_acl(self.root)
        return manifest

    def _profile_path(self, filename):
        require(isinstance(filename, str), "PROFILE_PATH_INVALID")
        return life.safe_child(self.clash_dir / "profiles", filename, must_exist=True)

    def _bridge_mapping(self):
        if not self.bridge_root.exists(): return {}
        try:
            manifest = life.read_json(life.safe_child(self.bridge_root, "manifest.json", must_exist=True))
            require(manifest.get("kind") == "codex-feiniao-bridge", "BRIDGE_INVALID")
            mapping = life.read_json(life.safe_child(self.bridge_root, "port-map.json", must_exist=True))
            values = mapping.get("entries", []) if isinstance(mapping, dict) else mapping
            return {entry.get("nodeName"): entry.get("port") for entry in values if isinstance(entry, dict)}
        except life.BridgeError:
            return {}

    def _resolve_nodes(self, config):
        nodes = copy.deepcopy(config.get("proxies", []))
        require(isinstance(nodes, list), "PROXIES_INVALID")
        unresolved = False
        providers = config.get("proxy-providers", {})
        require(isinstance(providers, dict), "PROVIDER_INVALID")
        for provider in providers.values():
            require(isinstance(provider, dict), "PROVIDER_INVALID")
            if provider.get("type") == "inline":
                extra = provider.get("payload", [])
            else:
                path = provider.get("path")
                if not isinstance(path, str):
                    unresolved = True; continue
                try:
                    cached = life.safe_child(self.clash_dir, path, must_exist=True)
                    extra = _read_yaml(cached).get("proxies", [])
                except life.BridgeError:
                    unresolved = True; continue
            require(isinstance(extra, list), "PROVIDER_INVALID")
            nodes.extend(copy.deepcopy(extra))
        require(not unresolved, "PROVIDER_CACHE_UNAVAILABLE")
        nodes = [node for node in nodes if not (isinstance(node, dict) and
                 (node.get("type") in {"direct", "reject", "reject-drop", "pass", "compatible"}
                  or (isinstance(node.get("name"), str) and (re.fullmatch(r"\*.*官网.*\*", node["name"])
                      or any(tag in node["name"] for tag in ("剩余流量", "套餐到期", "订阅更新", "到期时间"))))))]
        require(nodes and all(valid_node(node) for node in nodes), "NO_VALID_NODES")
        require(len({node["name"] for node in nodes}) == len(nodes), "DUPLICATE_NODE_NAME")
        require(not any(node.get("dialer-proxy") for node in nodes), "CHAINED_NODE_REQUIRES_REVIEW")
        return nodes

    def _routing_snapshot(self, items, current):
        # Runtime is used as a read-only effective rule snapshot. The hub never
        # invokes a main-core API mutation or writes its runtime/profile files.
        runtime = self.clash_dir / "clash-verge.yaml"
        if runtime.is_file():
            base = clean_routing(_read_yaml(runtime))
        else:
            item = next((item for item in items if item.get("uid") == current), None)
            require(item is not None, "CURRENT_RULES_UNAVAILABLE")
            base = clean_routing(_read_yaml(self._profile_path(item.get("file"))))
        require(isinstance(base.get("rules"), list) and base["rules"], "CURRENT_RULES_UNAVAILABLE")
        return base

    def _reviewed_policy(self, current_item):
        reviewed = {}
        old_root = Path(os.environ.get("LOCALAPPDATA", "")) / "CodexClashZeroOmega"
        old_registry = old_root / "registry.json"
        if old_registry.is_file() and current_item is not None:
            try:
                old = life.read_json(old_registry)
                legacy_id = hashlib.sha256(json.dumps([current_item["uid"], current_item.get("url", ""), current_item.get("type")], ensure_ascii=False, sort_keys=True).encode("utf8")).hexdigest()
                value = old.get("policies", {}).get(legacy_id, {})
                reviewed = {name: action for name, action in value.items() if action in ACTIONS}
            except (life.BridgeError, ValueError, TypeError):
                pass
        if self.root.exists() and self._path("registry.json").is_file():
            registry = life.read_json(self._path("registry.json"))
            rules_id = digest(self._base.get("rules", []))
            reviewed.update(registry.get("policy", {}).get(rules_id, {}))
        return reviewed

    @safe_public
    def discover(self):
        profiles = _read_yaml(self.clash_dir / "profiles.yaml")
        items = profiles.get("items", [])
        require(isinstance(items, list), "PROFILES_INVALID")
        self._current = profiles.get("current")
        self._sources = {}; summaries = []
        bridge_mapping = self._bridge_mapping()
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("type") not in {"remote", "local"}: continue
            source_id = source_identity(item) if isinstance(item.get("uid"), str) and item["uid"] else digest(["invalid-profile", index])
            name = safe_display_name(item.get("name"), "未命名订阅")
            summary = {"id": source_id, "name": name, "type": item["type"], "nodeCount": 0, "available": False,
                       "reason": None, "isFeiniao": False, "current": item.get("uid") == self._current}
            try:
                require(isinstance(item.get("uid"), str) and item["uid"], "SOURCE_INVALID")
                config = _read_yaml(self._profile_path(item.get("file")))
                nodes = self._resolve_nodes(config)
                possible_bridge = [node for node in nodes if node["type"] == "socks5" and node["server"] == "127.0.0.1" and 40000 <= node["port"] <= 40107]
                matched = [node for node in nodes if node["type"] == "socks5" and node["server"] == "127.0.0.1"
                           and bridge_mapping.get(node["name"]) == node["port"]]
                require(not possible_bridge or len(possible_bridge) == len(matched), "BRIDGE_NOT_VERIFIED")
                is_feiniao = bool(matched)
                if is_feiniao:
                    require(len(matched) == len(nodes), "PARTIAL_BRIDGE_PROFILE")
                summary.update({"nodeCount": len(nodes), "available": True, "isFeiniao": is_feiniao})
                self._sources[source_id] = {"id": source_id, "name": name, "nodes": nodes, "isFeiniao": is_feiniao,
                                            "sourceFileHash": life.file_hash(self._profile_path(item.get("file")))}
            except life.BridgeError as error:
                summary["reason"] = error.code
            except Exception:
                summary["reason"] = "PROFILE_UNREADABLE"
            summaries.append(summary)
        self._base = self._routing_snapshot(items, self._current)
        current_item = next((item for item in items if isinstance(item, dict) and item.get("uid") == self._current), None)
        reviewed = self._reviewed_policy(current_item)
        self._policy, issues = assess_policy(self._base, reviewed)
        self._rules_hash = digest([self._base.get("rules", []), self._base.get("sub-rules", {})])
        return {"ok": True, "sources": summaries, "ruleSummary": {"count": len(self._base["rules"]), "fallback": "PROXY", "origin": "当前 Clash 生效规则快照", "snapshotId": self._rules_hash[:12]},
                "policyIssues": issues}

    @safe_public
    def preview(self, source_ids=None):
        """Safe node rows, without allocating ports or creating private state."""
        if not self._sources:
            discovered = self.discover()
            require(discovered.get("ok"), discovered.get("code", "DISCOVERY_FAILED"))
        source_ids = list(self._sources) if source_ids is None else source_ids
        require(isinstance(source_ids, (list, tuple)) and all(identity in self._sources for identity in source_ids), "SOURCE_UNAVAILABLE")
        registry = {"schema": SCHEMA, "entries": []}
        if self.root.exists() and self._path("registry.json").is_file():
            self._ensure_root(); registry = life.read_json(self._path("registry.json", True))
        known = {entry["id"] for entry in registry["entries"]}
        entries = assign_entries(registry, [self._sources[identity] for identity in source_ids])
        selected = set(source_ids)
        rows = self._safe_entries([entry for entry in entries if entry["active"] and entry["sourceId"] in selected])
        for row in rows:
            if row["id"] not in known: row["port"] = None
            row["status"] = "untested"
        return {"ok": True, "rows": rows}

    def _find_core(self):
        if self.core_path:
            require(self.core_path.is_file(), "CORE_NOT_FOUND")
            return self.core_path
        candidates = [Path(r"G:\Clash Verge\verge-mihomo.exe"), Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "mihomo.exe"]
        if os.name == "nt":
            values = life._powershell("$v=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'verge-mihomo.exe'} | ForEach-Object {$_.ExecutablePath}); ConvertTo-Json -InputObject $v -Compress", {})
            if isinstance(values, list): candidates = [Path(value) for value in values if isinstance(value, str)] + candidates
        for path in candidates:
            if path.is_file(): return path.resolve()
        raise HubError("CORE_NOT_FOUND")

    def _install_core(self):
        source = self._find_core()
        destination = self._path("bin/mihomo.exe")
        try:
            source_hash = life.file_hash(source)
        except OSError:
            raise HubError("CORE_FILE_UNREADABLE") from None
        if destination.is_file():
            try:
                installed_hash = life.file_hash(destination)
            except OSError:
                raise HubError("CORE_FILE_UNREADABLE") from None
            require(installed_hash == source_hash, "CORE_UPDATE_REQUIRES_EXPLICIT_ACTION")
        else:
            temporary = self._path("bin/core-copy-" + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(source, temporary)
                require(life.file_hash(temporary) == source_hash, "CORE_COPY_CHANGED")
                require(not destination.exists(), "CORE_COPY_RACE")
                os.replace(temporary, destination)
            except OSError:
                raise HubError("CORE_COPY_FAILED") from None
            finally:
                temporary.unlink(missing_ok=True)
        for source_directory in (self.clash_dir, self.clash_dir / "data", source.parent):
            if not source_directory.is_dir(): continue
            for filename in ("geoip.dat", "geosite.dat", "country.mmdb", "GeoIP.dat", "GeoSite.dat", "Country.mmdb"):
                data = source_directory / filename
                if data.is_file() and not self._path("data/" + filename).exists():
                    shutil.copyfile(data, self._path("data/" + filename))
        return destination

    def _rule_providers(self, base):
        result = copy.deepcopy(base.get("rule-providers", {}))
        require(isinstance(result, dict), "RULE_PROVIDER_INVALID")
        for name, provider in result.items():
            require(isinstance(name, str) and isinstance(provider, dict), "RULE_PROVIDER_INVALID")
            if provider.get("type") == "inline": continue
            cache_path = provider.get("path")
            require(isinstance(cache_path, str), "RULE_PROVIDER_CACHE_UNAVAILABLE")
            source = life.safe_child(self.clash_dir, cache_path, must_exist=True)
            target = self._path("data/rules-" + digest(name)[:24] + source.suffix)
            if not target.exists() or life.file_hash(source) != life.file_hash(target): shutil.copyfile(source, target)
            provider["path"] = str(target)
            # Cache snapshots make preparation predictable. The main Clash owns
            # its subscription/rule updates; this tool only refreshes on prepare.
            provider["type"] = "file"; provider.pop("url", None); provider.pop("interval", None)
        return result

    def _compile_nodes(self, sources, entries):
        active = {entry["id"]: entry for entry in entries if entry["active"]}
        result = []
        for source in sources:
            fingerprints = Counter(node_identity(source["id"], node) for node in source["nodes"])
            names = {}
            for original in source["nodes"]:
                identity = node_identity(source["id"], original)
                if fingerprints[identity] > 1: identity = digest([identity, original["name"]])
                names[original["name"]] = active[identity]["internalName"]
            for original in source["nodes"]:
                node = copy.deepcopy(original); node["name"] = names[original["name"]]
                if node.get("dialer-proxy"):
                    require(node["dialer-proxy"] in names, "DIALER_GROUP_REVIEW_REQUIRED")
                    node["dialer-proxy"] = names[node["dialer-proxy"]]
                result.append(node)
        return result

    def _validate(self, config_path):
        with life._child_dll_search(), self._path("logs/validation.private.log").open("wb") as log:
            process = subprocess.run([str(self._path("bin/mihomo.exe", True)), "-t", "-d", str(self._path("data")), "-f", str(config_path)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=60,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        require(process.returncode == 0, "CORE_VALIDATION_FAILED")

    @safe_public
    def prepare(self, source_ids, policy_override=None):
        require(isinstance(source_ids, (list, tuple)) and source_ids and len(set(source_ids)) == len(source_ids), "SELECT_SUBSCRIPTIONS")
        discovered = self.discover()
        require(discovered.get("ok"), discovered.get("code", "DISCOVERY_FAILED"))
        require(all(identity in self._sources for identity in source_ids), "SOURCE_UNAVAILABLE")
        reviewed = dict(self._policy)
        if policy_override is not None:
            require(isinstance(policy_override, dict) and all(value in ACTIONS for value in policy_override.values()), "POLICY_INVALID")
            reviewed.update(policy_override)
        policy, issues = assess_policy(self._base, reviewed)
        if issues: return {"ok": True, "prepared": False, "policyIssues": issues}
        self._ensure_root()
        with life.operation_lock(self.root):
            self._install_core()
            registry = life.read_json(self._path("registry.json", True))
            selected = [self._sources[identity] for identity in sorted(source_ids)]
            excluded = excluded_ports()
            entries = assign_entries(registry, selected, lambda port: port not in excluded and _port_available(port))
            candidate_base = {"proxies": self._compile_nodes(selected, entries), "rules": copy.deepcopy(self._base["rules"]),
                              "sub-rules": copy.deepcopy(self._base.get("sub-rules", {})),
                              "rule-providers": self._rule_providers(self._base)}
            config = convert_routing(candidate_base, entries, policy)
            dns = copy.deepcopy(self._base.get("dns", {}))
            if not isinstance(dns, dict): dns = {}
            dns.pop("listen", None); dns.pop("fake-ip-range", None); dns.pop("fake-ip-range6", None)
            dns["enhanced-mode"] = "redir-host"
            config.update({"mode": "rule", "allow-lan": False, "bind-address": "127.0.0.1", "ipv6": bool(self._base.get("ipv6", False)),
                           "log-level": "warning", "tun": {"enable": False}, "dns": dns,
                           "profile": {"store-selected": False, "store-fake-ip": False}, "sniffer": {"enable": False},
                           "external-controller-pipe": r"\\.\pipe\codex-msh-" + self._ensure_root()["installId"],
                           "secret": self._ensure_root()["controllerSecret"], "geodata-loader": self._base.get("geodata-loader", "memconservative")})
            token = uuid.uuid4().hex
            directory = self._path("prepared/" + token); directory.mkdir()
            candidate = directory / "config.yaml"
            _atomic_yaml(candidate, config)
            self._validate(candidate)
            record = {"schema": SCHEMA, "token": token, "selected": list(source_ids), "entries": entries,
                      "configHash": life.file_hash(candidate), "rulesHash": self._rules_hash, "dependency": any(source["isFeiniao"] for source in selected)}
            life.atomic_json(directory / "record.json", record)
            # Only our mapping is changed. Entries remain reserved for life.
            registry["entries"] = entries
            registry.setdefault("policy", {})[digest(self._base.get("rules", []))] = policy
            life.atomic_json(self._path("registry.json"), registry)
            life.atomic_json(self._path("last-prepared.json"), {"token": token})
            self._last_prepared = token
        return {"ok": True, "prepared": True, "token": token, "counts": {"sources": len(selected), "active": sum(entry["active"] for entry in entries),
                "retained": sum(not entry["active"] for entry in entries), "total": len(entries)}, "entries": self._safe_entries(entries),
                "currentFlags": {"mainClashUnchanged": True, "requiresBridge": record["dependency"], "needsApply": True}, "policyIssues": []}

    @staticmethod
    def _safe_entries(entries):
        return [{"nodeId": entry["id"], "id": entry["id"], "name": safe_display_name(entry["nodeName"], "节点"), "profileName": entry["profileName"],
                 "sourceId": entry["sourceId"], "source": safe_display_name(entry["sourceName"], "订阅"), "port": entry["port"], "active": entry["active"]} for entry in entries]

    def _prepared_record(self, prepared=None):
        if isinstance(prepared, dict): token = prepared.get("token")
        elif isinstance(prepared, str): token = prepared
        else: token = self._last_prepared
        if not token and self._path("last-prepared.json").is_file(): token = life.read_json(self._path("last-prepared.json"))["token"]
        require(isinstance(token, str) and re.fullmatch(r"[0-9a-f]{32}", token), "NOT_PREPARED")
        directory = self._path("prepared/" + token)
        record = life.read_json(life.safe_child(directory, "record.json", must_exist=True))
        candidate = life.safe_child(directory, "config.yaml", must_exist=True)
        require(record.get("token") == token and record.get("configHash") == life.file_hash(candidate), "PREPARED_CHANGED")
        require(isinstance(record.get("selected"), list) and record["selected"]
                and len(record["selected"]) == len(set(record["selected"]))
                and all(isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity) for identity in record["selected"]), "PREPARED_INVALID")
        require(type(record.get("dependency")) is bool, "PREPARED_INVALID")
        return record, candidate

    def _state(self):
        if not self._path("state.json").is_file(): return None
        return life.read_json(self._path("state.json"))

    def _actual(self, state):
        return life._actual(state["pid"]) if isinstance(state, dict) and type(state.get("pid")) is int else None

    def _identity(self, state, actual):
        if not isinstance(state, dict) or not isinstance(actual, dict): return False
        if not (type(state.get("pid")) is int and state["pid"] > 0 and type(actual.get("pid")) is int
                and isinstance(state.get("creation_time"), str) and re.fullmatch(r"[1-9][0-9]*", state["creation_time"])
                and isinstance(actual.get("creation_time"), str) and re.fullmatch(r"[1-9][0-9]*", actual["creation_time"])): return False
        executable, config = self._path("bin/mihomo.exe"), self._path("config.yaml")
        argv = [str(executable.resolve()), "-d", str(self._path("data").resolve()), "-f", str(config.resolve())]
        return (state.get("pid") == actual.get("pid") and state.get("creation_time") == actual.get("creation_time")
                and actual.get("argv") == argv and Path(actual.get("exe", "")).resolve() == executable.resolve()
                and state.get("installId") == self._ensure_root()["installId"]
                and state.get("exeHash") == life.file_hash(executable) and state.get("configHash") == life.file_hash(config))

    def _dependency_command(self, action):
        require(action in {"start", "stop", "status"}, "BRIDGE_ACTION_INVALID")
        # The audited bridge lifecycle is bundled with this software. Only its
        # existing private core/configuration are needed; no CMD or old public
        # executable is launched. Ownership and exact process checks stay intact.
        result = getattr(life, action)(self.bridge_root)
        require(isinstance(result, dict) and result.get("ok"), "BRIDGE_COMMAND_FAILED")
        return result

    def _dependency_start(self, required):
        if not required: return None
        result = self._dependency_command("status")
        running = bool(result.get("running"))
        if running: require(result.get("identity_matches") and result.get("all_listeners_private"), "BRIDGE_IDENTITY_MISMATCH")
        else:
            result = self._dependency_command("start")
            require(result.get("running") and result.get("all_listeners_private"), "BRIDGE_START_FAILED")
        state = life.read_json(life.safe_child(self.bridge_root, "state.json", must_exist=True))
        return {"owned": not running, "pid": state["pid"], "creation_time": state["creation_time"], "exeHash": state.get("exe_sha256"), "installId": state.get("install_id")}

    def _dependency_stop(self, dependency):
        if not isinstance(dependency, dict) or not dependency.get("owned"): return
        current = life.read_json(life.safe_child(self.bridge_root, "state.json", must_exist=True))
        # Only stop the precise instance this hub started, never a replacement.
        if (current.get("pid") == dependency.get("pid") and current.get("creation_time") == dependency.get("creation_time")
            and current.get("exe_sha256") == dependency.get("exeHash") and current.get("install_id") == dependency.get("installId")):
            self._dependency_command("stop")

    def _stop_own(self, state, stop_dependency=True):
        if not isinstance(state, dict) or state.get("status") == "stopped": return
        try:
            with life.ProcessHandle(state["pid"], terminate=True) as process:
                actual = process.snapshot()
                if not life.previous_instance_ended(state, actual):
                    require(self._identity(state, actual), "PROCESS_IDENTITY_MISMATCH")
                    process.stop()
        except life.BridgeError as error:
            if error.code != "PROCESS_NOT_FOUND": raise
        if stop_dependency: self._dependency_stop(state.get("dependency"))
        life.atomic_json(self._path("state.json"), {"status": "stopped", "installId": self._ensure_root()["installId"]})

    def _launch(self, record, dependency):
        ports = [entry["port"] for entry in record["entries"]]
        require(not life.listener_rows(0, ports), "PORT_OCCUPIED")
        require(all(_port_available(port) for port in ports), "PORT_UNAVAILABLE")
        process = None
        try:
            executable = self._path("bin/mihomo.exe", True).resolve(); config = self._path("config.yaml", True).resolve()
            argv = [str(executable), "-d", str(self._path("data").resolve()), "-f", str(config)]
            with life._child_dll_search(), self._path("logs/runtime.private.log").open("ab") as log:
                process = subprocess.Popen(argv, cwd=str(self.root), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            actual = life._actual(process.pid)
            require(actual is not None, "CORE_START_FAILED")
            state = {"status": "running", "pid": process.pid, "creation_time": actual["creation_time"], "installId": self._ensure_root()["installId"],
                     "exeHash": life.file_hash(executable), "configHash": life.file_hash(config), "token": record["token"],
                     "entries": record["entries"], "selected": record["selected"], "dependency": dependency}
            require(self._identity(state, actual), "PROCESS_IDENTITY_MISMATCH")
            deadline = time.monotonic() + 25
            while process.poll() is None and time.monotonic() < deadline:
                rows = life.listener_rows(process.pid, ports)
                if life.listeners_match(rows, process.pid, ports):
                    life.atomic_json(self._path("state.json"), state)
                    return state
                require(all(row.get("pid") == process.pid and row.get("address") == "127.0.0.1" and row.get("port") in ports for row in rows), "UNEXPECTED_LISTENER")
                time.sleep(0.3)
            raise HubError("CORE_START_FAILED")
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            raise

    @safe_public
    def start(self, prepared=None):
        self._ensure_root()
        record, candidate = self._prepared_record(prepared)
        with life.operation_lock(self.root):
            self._validate(candidate)
            old_state = self._state()
            if old_state and old_state.get("status") != "stopped":
                actual = self._actual(old_state)
                if not life.previous_instance_ended(old_state, actual):
                    require(self._identity(old_state, actual), "PROCESS_IDENTITY_MISMATCH")
                    if old_state.get("configHash") == record["configHash"]:
                        require(life.listeners_match(life.listener_rows(old_state["pid"], [entry["port"] for entry in old_state["entries"]]), old_state["pid"], [entry["port"] for entry in old_state["entries"]]), "LISTENER_IDENTITY_MISMATCH")
                        old_state.update({"token": record["token"], "entries": record["entries"], "selected": record["selected"]})
                        life.atomic_json(self._path("state.json"), old_state)
                        life.atomic_json(self._path("applied.json"), record)
                        return {"ok": True, "running": True, "unchanged": True, "allListenersPrivate": True, "active": sum(entry["active"] for entry in old_state["entries"]), "sourceIds": record["selected"]}
            config_path = self._path("config.yaml")
            backup = self._path("backups/" + uuid.uuid4().hex); backup.mkdir()
            if config_path.exists(): shutil.copyfile(config_path, backup / "config.yaml")
            if old_state: life.atomic_json(backup / "state.json", old_state)
            dependency = old_state.get("dependency") if isinstance(old_state, dict) else None
            old_running = bool(old_state and old_state.get("status") != "stopped" and self._actual(old_state))
            if old_running: self._stop_own(old_state, stop_dependency=False)
            try:
                if record["dependency"]:
                    fresh = self._dependency_start(True)
                    same_dependency = (isinstance(dependency, dict) and dependency.get("owned")
                                       and all(dependency.get(key) == fresh.get(key) for key in ("pid", "creation_time", "exeHash", "installId")))
                    if not same_dependency: dependency = fresh
                elif dependency:
                    self._dependency_stop(dependency); dependency = None
                shutil.copyfile(candidate, config_path)
                state = self._launch(record, dependency)
                life.atomic_json(self._path("applied.json"), record)
            except BaseException:
                restored = False
                if old_running and (backup / "config.yaml").is_file():
                    shutil.copyfile(backup / "config.yaml", config_path)
                    old_record = life.read_json(self._path("applied.json", True))
                    restored_dependency = old_state.get("dependency")
                    if old_record["dependency"]:
                        fresh = self._dependency_start(True)
                        same_dependency = (isinstance(restored_dependency, dict) and restored_dependency.get("owned")
                                           and all(restored_dependency.get(key) == fresh.get(key) for key in ("pid", "creation_time", "exeHash", "installId")))
                        if not same_dependency: restored_dependency = fresh
                    else:
                        self._dependency_stop(dependency); restored_dependency = None
                    self._launch(old_record, restored_dependency); restored = True
                elif dependency: self._dependency_stop(dependency)
                if not restored: life.atomic_json(self._path("state.json"), {"status": "stopped", "installId": self._ensure_root()["installId"]})
                raise
        return {"ok": True, "running": True, "unchanged": False, "allListenersPrivate": True,
                "active": sum(entry["active"] for entry in state["entries"]), "retained": sum(not entry["active"] for entry in state["entries"]),
                "bridgeRunning": bool(dependency), "bridgeOwned": bool(dependency and dependency.get("owned")), "mainClashUnchanged": True,
                "sourceIds": record["selected"]}

    @safe_public
    def stop(self):
        if not self.root.exists(): return {"ok": True, "running": False, "unchanged": True}
        self._ensure_root()
        with life.operation_lock(self.root):
            state = self._state()
            self._stop_own(state)
        return {"ok": True, "running": False, "mainClashUnchanged": True}

    @safe_public
    def status(self):
        if not self.root.exists(): return {"ok": True, "installed": False, "running": False, "entries": []}
        self._ensure_root()
        state = self._state()
        registry = life.read_json(self._path("registry.json", True))
        running, healthy = False, False
        if state and state.get("status") != "stopped":
            actual = self._actual(state)
            if not life.previous_instance_ended(state, actual):
                require(self._identity(state, actual), "PROCESS_IDENTITY_MISMATCH")
                running = True
                ports = [entry["port"] for entry in state["entries"]]
                healthy = life.listeners_match(life.listener_rows(state["pid"], ports), state["pid"], ports)
        token = None
        if self._path("last-prepared.json").is_file(): token = life.read_json(self._path("last-prepared.json")).get("token")
        live_entries = state["entries"] if running else []
        return {"ok": True, "installed": True, "running": running, "allListenersPrivate": healthy,
                "pendingChanges": bool(token and (not running or state.get("token") != token)), "entries": self._safe_entries(registry["entries"]),
                "liveEntries": self._safe_entries(live_entries), "sourceIds": state.get("selected", []) if running else [],
                "active": sum(entry["active"] for entry in registry["entries"]), "retained": sum(not entry["active"] for entry in registry["entries"]),
                "bridgeOwned": bool(state and state.get("dependency", {}).get("owned")) if state and state.get("dependency") else False}

    def _probe(self, entry, controller, domain="www.gstatic.com", path="/generate_204", expected_status=204):
        row = {"nodeId": entry["id"], "name": safe_display_name(entry["nodeName"], "节点"), "source": safe_display_name(entry["sourceName"], "订阅"), "port": entry["port"],
               "status": "inactive" if not entry["active"] else "failed", "latency": None, "routeVerified": False}
        if not entry["active"]: return row
        started = time.perf_counter()
        raw = None

        def exact(stream, count):
            data = b""
            while len(data) < count:
                chunk = stream.recv(count - len(data))
                if not chunk: raise OSError("closed")
                data += chunk
            return data

        try:
            raw = socket.create_connection(("127.0.0.1", entry["port"]), timeout=12)
            raw.sendall(b"\x05\x01\x00")
            require(exact(raw, 2) == b"\x05\x00", "SOCKS_NEGOTIATION_FAILED")
            host = domain.encode("idna")
            raw.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack("!H", 443))
            response = exact(raw, 4)
            require(response[1] == 0, "SOCKS_CONNECTION_FAILED")
            if response[3] == 1: exact(raw, 6)
            elif response[3] == 4: exact(raw, 18)
            elif response[3] == 3: exact(raw, exact(raw, 1)[0] + 2)
            else: raise HubError("SOCKS_RESPONSE_INVALID")
            with ssl.create_default_context().wrap_socket(raw, server_hostname=domain) as tls:
                connections = api("GET", "/connections", config=controller, timeout=8)[1].get("connections", [])
                listener = NS + "in." + entry["id"][:24]
                matching = [connection for connection in connections if connection.get("metadata", {}).get("inboundName") == listener and connection.get("metadata", {}).get("host") == domain]
                if not matching:
                    source_port = tls.getsockname()[1]
                    matching = [connection for connection in connections if str(connection.get("metadata", {}).get("sourcePort")) == str(source_port) and connection.get("metadata", {}).get("host") == domain]
                chain = matching[-1].get("chains", []) if matching else []
                route_ok = entry["internalName"] in chain and "DIRECT" not in chain
                tls.sendall(("GET " + path + " HTTP/1.1\r\nHost: " + domain + "\r\nConnection: close\r\n\r\n").encode())
                first = tls.recv(2048).split(b"\r\n", 1)[0]
                parts = first.split()
                status = int(parts[1]) if len(parts) > 1 else 0
                row.update({"httpStatus": status, "routeVerified": route_ok})
                if status == expected_status and route_ok:
                    row.update({"status": "available", "latency": round((time.perf_counter() - started) * 1000)})
                else: row["code"] = "ROUTE_UNVERIFIED" if not route_ok else "HTTP_UNEXPECTED"
        except socket.timeout: row.update({"status": "timeout", "code": "TIMEOUT"})
        except Exception: row["code"] = "CONNECTIVITY_FAILED"
        finally:
            if raw is not None: raw.close()
        return row

    @safe_public
    def test_nodes(self, progress=None):
        state = self._state()
        require(state and state.get("status") == "running" and self._identity(state, self._actual(state)), "CORE_NOT_RUNNING")
        record, _ = self._prepared_record()
        require(state.get("token") == record["token"], "NOT_APPLIED")
        controller = _read_yaml(self._path("config.yaml", True))
        rows = []; entries = state["entries"]
        # Four tiny HTTPS requests concurrently, without changing any selector.
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(self._probe, entry, controller): entry["id"] for entry in entries}
            for future in as_completed(futures):
                row = future.result(); rows.append(row)
                if progress is not None: progress(row)
        rows.sort(key=lambda row: row["port"])
        result = {"ok": True, "rows": rows, "tested": sum(entry["active"] for entry in entries),
                  "passed": sum(row["status"] == "available" for row in rows), "target": "HTTPS 204 + 固定节点路由核验", "automaticSwitch": False}
        life.atomic_json(self._path("logs/connectivity.private.json"), result)
        return result

    @safe_public
    def export_bak(self, output_dir):
        self._ensure_root()
        record, _ = self._prepared_record()
        state = self._state()
        require(state and state.get("status") == "running" and state.get("token") == record["token"]
                and self._identity(state, self._actual(state)), "NOT_APPLIED")
        ports = [entry["port"] for entry in state["entries"]]
        require(life.listeners_match(life.listener_rows(state["pid"], ports), state["pid"], ports), "LISTENER_IDENTITY_MISMATCH")
        directory = Path(output_dir).absolute().resolve()
        require(not directory.is_relative_to(self.root), "PUBLIC_EXPORT_MUST_BE_OUTSIDE_PRIVATE")
        life.safe_child(directory.parent, directory.name)
        directory.mkdir(parents=True, exist_ok=True)
        backup = build_backup(record["entries"])
        path = life.safe_child(directory, BAK_NAME)
        data = json.dumps(backup, ensure_ascii=False, indent=2).encode("utf8")
        mapping_path = life.safe_child(directory, "多订阅-节点与端口映射表.md")
        text = "# 多订阅浏览器入口\n\n每个入口只绑定一条线路；失效或未选入的入口拒绝请求，不自动切换。\n\n|订阅|节点 / ZeroOmega 情景名|本地 SOCKS5|状态|\n|---|---|---|---|\n"
        def cell(value): return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
        for entry in record["entries"]:
            text += "|" + cell(safe_display_name(entry["sourceName"], "订阅")) + "|" + cell(entry["profileName"]) + "|127.0.0.1:" + str(entry["port"]) + "|" + ("已选入" if entry["active"] else "保留，拒绝") + "|\n"
        # Preserve previously exported artifacts in the private recovery area.
        for target in (path, mapping_path):
            if target.exists(): shutil.copyfile(target, self._path("backups/export-" + uuid.uuid4().hex + target.suffix))
        temporary = life.safe_child(directory, path.name + ".tmp"); temporary.write_bytes(data); os.replace(temporary, path)
        temporary = life.safe_child(directory, mapping_path.name + ".tmp"); temporary.write_text(text, encoding="utf8"); os.replace(temporary, mapping_path)
        return {"ok": True, "files": {"bak": str(path), "mapping": str(mapping_path)}, "profiles": len(record["entries"]),
                "onlyLoopback": True, "needsRunningBackend": True}
