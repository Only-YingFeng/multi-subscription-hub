"""Synthetic multi-subscription contracts; never touch a user's running core.

The optional MSH_TEST_CORE check executes only ``mihomo -t`` against a temporary
synthetic configuration. It does not start a listener or reload an existing core.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager, nullcontext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("hub_backend_contract", HERE / "backend.py")
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)


def node(name="美国中转815", server="192.0.2.10", credential="SYNTHETIC_SECRET_DO_NOT_EXPORT"):
    return {"name": name, "type": "trojan", "server": server, "port": 443,
            "password": credential, "sni": "edge.example.invalid", "udp": False}


def source(uid="source-a", name="订阅一", nodes=None, url=None):
    item = {"uid": uid, "type": "remote", "name": name,
            "url": url or f"https://subscription.example.invalid/{uid}/SYNTHETIC_URL_TOKEN"}
    return {"id": backend.source_identity(item), "name": name,
            "nodes": copy.deepcopy(nodes if nodes is not None else [node()])}


def registry(entries=None):
    return {"schema": 1, "entries": copy.deepcopy(entries or []), "policy": {}}


def routing_bundle():
    sources = [source(), source("source-b", "订阅二", [node(server="198.51.100.22")])]
    entries = backend.assign_entries(registry(), sources)
    compiled = []
    for entry in entries:
        original = next(item for item in sources if item["id"] == entry["sourceId"])["nodes"][0]
        compiled.append({**copy.deepcopy(original), "name": entry["internalName"]})
    base = {"proxies": compiled, "proxy-groups": [{"name": "unused-auto", "type": "url-test", "proxies": [compiled[0]["name"]]}],
            "rules": ["DOMAIN,direct.example.invalid,Local", "DOMAIN,blocked.example.invalid,Block",
                      "DOMAIN-SUFFIX,proxy.example.invalid,Internet", "IP-CIDR,203.0.113.0/24,Internet,no-resolve",
                      "AND,((DOMAIN,logical.example.invalid),(NETWORK,tcp)),Internet", "MATCH,Other"],
            "sub-rules": {}}
    policy = {"Local": "DIRECT", "Block": "REJECT", "Internet": "PROXY", "Other": "PROXY"}
    return base, entries, policy


@contextmanager
def export_ready(hub, entries):
    """Only synthetic process and listener proofs; no real process is queried."""
    token = "a" * 32
    record = {"entries": entries, "token": token}
    state = {"status": "running", "token": token, "pid": 1234, "entries": entries}
    rows = [{"pid": 1234, "address": "127.0.0.1", "port": entry["port"]} for entry in entries]
    with patch.object(hub, "_ensure_root"), \
         patch.object(hub, "_prepared_record", return_value=(record, None)), \
         patch.object(hub, "_state", return_value=state), patch.object(hub, "_actual", return_value={}), \
         patch.object(hub, "_identity", return_value=True), \
         patch.object(backend.life, "listener_rows", return_value=rows):
        yield


class SourceIdentityContracts(unittest.TestCase):
    def test_subscription_display_name_and_order_do_not_change_identity(self):
        item = {"uid": "same-uid", "type": "remote", "name": "old name",
                "url": "https://subscription.example.invalid/stable"}
        changed = {"url": item["url"], "name": "new name", "type": "remote", "uid": "same-uid"}
        self.assertEqual(backend.source_identity(item), backend.source_identity(changed))

    def test_changed_subscription_url_same_uid_cannot_reuse_old_identity(self):
        item = {"uid": "same-uid", "type": "remote", "name": "same display",
                "url": "https://subscription.example.invalid/airport-a"}
        self.assertNotEqual(backend.source_identity(item), backend.source_identity({**item, "url": "https://subscription.example.invalid/airport-b"}))

    def test_distinct_uid_or_kind_has_distinct_identity(self):
        item = {"uid": "uid-a", "type": "remote", "url": "https://subscription.example.invalid/a"}
        self.assertNotEqual(backend.source_identity(item), backend.source_identity({**item, "uid": "uid-b"}))
        self.assertNotEqual(backend.source_identity(item), backend.source_identity({**item, "type": "local"}))

    def test_identity_is_an_opaque_fingerprint(self):
        result = source()["id"]
        self.assertRegex(result, r"^[0-9a-f]{64}$")
        self.assertNotIn("SYNTHETIC_URL_TOKEN", result)
        self.assertNotIn("subscription.example.invalid", result)

    def test_node_rename_and_mapping_key_order_do_not_change_identity(self):
        original = node()
        renamed = dict(reversed(list(original.items())))
        renamed["name"] = "美国中转815 新名称"
        source_id = source()["id"]
        self.assertEqual(backend.node_identity(source_id, original), backend.node_identity(source_id, renamed))

    def test_endpoint_protocol_and_transport_changes_are_new_nodes(self):
        original = node()
        source_id = source()["id"]
        for changed in ({"server": "198.51.100.22"}, {"port": 8443},
                        {"type": "anytls"},
                        {"sni": "other.example.invalid"}):
            with self.subTest(change=next(iter(changed))):
                self.assertNotEqual(backend.node_identity(source_id, original), backend.node_identity(source_id, {**original, **changed}))

    def test_same_name_same_parameters_across_sources_are_isolated(self):
        self.assertNotEqual(backend.node_identity(source()["id"], node()), backend.node_identity(source("source-b")["id"], node()))

    def test_account_rotation_is_not_a_route_identity_change(self):
        original = node()
        source_id = source()["id"]
        for key in ("password", "uuid", "uid", "username"):
            with self.subTest(field=key):
                self.assertEqual(backend.node_identity(source_id, original), backend.node_identity(source_id, {**original, key: "ROTATED_SYNTHETIC_AUTH"}))


class PortRegistryContracts(unittest.TestCase):
    def test_inputs_are_not_mutated(self):
        prior = registry()
        sources = [source(), source("source-b", "订阅二", [node(server="198.51.100.22")])]
        expected = copy.deepcopy((prior, sources))
        entries = backend.assign_entries(prior, sources)
        self.assertEqual((prior, sources), expected)
        self.assertEqual(len(entries), 2)

    def test_new_ports_use_separate_range_and_unique_namespace(self):
        entries = backend.assign_entries(registry(), [source(), source("source-b", "订阅二")])
        self.assertEqual(len({entry["id"] for entry in entries}), 2)
        self.assertEqual(len({entry["internalName"] for entry in entries}), 2)
        self.assertEqual(len({entry["profileName"] for entry in entries}), 2)
        self.assertEqual(len({entry["port"] for entry in entries}), 2)
        self.assertTrue(all(46000 <= entry["port"] <= 65535 for entry in entries))
        self.assertTrue(all("美国中转815" in entry["profileName"] for entry in entries))
        self.assertTrue(all(entry["port"] not in range(20000, 20069) for entry in entries))

    def test_node_and_source_reorder_preserve_every_port(self):
        sources = [source(nodes=[node("线路甲"), node("线路乙", server="198.51.100.22")]), source("source-b", "订阅二")]
        first = backend.assign_entries(registry(), sources)
        shuffled = copy.deepcopy(list(reversed(sources)))
        for item in shuffled:
            item["nodes"].reverse()
        updated = backend.assign_entries(registry(first), shuffled)
        self.assertEqual({e["id"]: e["port"] for e in first}, {e["id"]: e["port"] for e in updated})

    def test_subscription_and_unique_node_rename_preserve_port(self):
        original = source()
        first = backend.assign_entries(registry(), [original])
        renamed = {**copy.deepcopy(original), "name": "订阅重新命名"}
        renamed["nodes"][0]["name"] = "美国中转815 改名"
        updated = backend.assign_entries(registry(first), [renamed])
        active = [entry for entry in updated if entry["active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], first[0]["id"])
        self.assertEqual(active[0]["port"], first[0]["port"])

    def test_removed_nodes_keep_ports_and_new_nodes_never_reuse_them(self):
        first = backend.assign_entries(registry(), [source(nodes=[node("旧线路"), node("保留线路", server="198.51.100.22")])])
        updated = backend.assign_entries(registry(first), [source(nodes=[node("保留线路", server="198.51.100.22"), node("新线路", server="203.0.113.33")])])
        by_id = {entry["id"]: entry for entry in updated}
        removed = next(entry for entry in first if entry["nodeName"] == "旧线路")
        self.assertFalse(by_id[removed["id"]]["active"])
        self.assertEqual(by_id[removed["id"]]["port"], removed["port"])
        new = next(entry for entry in updated if entry["nodeName"] == "新线路")
        self.assertGreater(new["port"], max(entry["port"] for entry in first))

    def test_replacing_url_retires_all_old_bindings_even_if_names_match(self):
        first = backend.assign_entries(registry(), [source()])
        replacement = source(url="https://subscription.example.invalid/new-airport")
        updated = backend.assign_entries(registry(first), [replacement])
        self.assertEqual(len(updated), 2)
        old = next(entry for entry in updated if entry["id"] == first[0]["id"])
        new = next(entry for entry in updated if entry["active"])
        self.assertFalse(old["active"])
        self.assertNotEqual(new["port"], old["port"])
        self.assertNotEqual(new["sourceId"], old["sourceId"])

    def test_credential_rotation_keeps_the_same_route_and_port(self):
        first = backend.assign_entries(registry(), [source()])
        replacement = source(nodes=[node(credential="ROTATED_SYNTHETIC_AUTH")])
        updated = backend.assign_entries(registry(first), [replacement])
        self.assertEqual(len(updated), 1)
        self.assertTrue(updated[0]["active"])
        self.assertEqual(updated[0]["id"], first[0]["id"])
        self.assertEqual(updated[0]["port"], first[0]["port"])

    def test_removed_source_keeps_only_dead_entries(self):
        first = backend.assign_entries(registry(), [source()])
        updated = backend.assign_entries(registry(first), [])
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["port"], first[0]["port"])
        self.assertFalse(updated[0]["active"])

    def test_exact_duplicate_node_names_are_rejected(self):
        with self.assertRaises(backend.HubError):
            backend.assign_entries(registry(), [source(nodes=[node(), node(server="198.51.100.22")])])

    def test_identical_endpoints_with_two_aliases_cannot_steal_each_others_port(self):
        original = source(nodes=[node("别名甲"), node("别名乙")])
        first = backend.assign_entries(registry(), [original])
        self.assertEqual(len({entry["id"] for entry in first}), 2)
        updated = backend.assign_entries(registry(first), [{**original, "nodes": list(reversed(original["nodes"]))}])
        self.assertEqual({e["nodeName"]: e["port"] for e in first}, {e["nodeName"]: e["port"] for e in updated if e["active"]})

    def test_new_allocations_skip_unavailable_ports(self):
        entries = backend.assign_entries(registry(), [source()], port_usable=lambda port: port not in {46000, 46001})
        self.assertEqual(entries[0]["port"], 46002)

    def test_historical_ports_are_not_reassigned_when_temporarily_unavailable(self):
        first = backend.assign_entries(registry(), [source()])
        updated = backend.assign_entries(registry(first), [source()], port_usable=lambda port: False)
        self.assertEqual(updated[0]["port"], first[0]["port"])

    def test_corrupt_duplicate_and_old_clash_ports_are_rejected(self):
        first = backend.assign_entries(registry(), [source()])
        for entries in ([{**first[0], "port": 20000}], [{**first[0], "port": True}],
                        [{**first[0], "port": 65536}], [first[0], first[0]],
                        [{**first[0], "id": "not-a-hash"}]):
            with self.subTest(case=entries[0]["port"]):
                with self.assertRaises(backend.HubError):
                    backend.assign_entries(registry(entries), [source()])

    def test_endpoint_change_keeps_old_port_dead(self):
        first = backend.assign_entries(registry(), [source()])
        updated = backend.assign_entries(registry(first), [source(nodes=[node(server="198.51.100.22")])])
        self.assertEqual(len(updated), 2)
        self.assertFalse(next(entry for entry in updated if entry["port"] == first[0]["port"])["active"])
        self.assertNotEqual(next(entry for entry in updated if entry["active"])["port"], first[0]["port"])

    def test_corrupt_internal_binding_is_rejected(self):
        first = backend.assign_entries(registry(), [source()])
        first[0]["internalName"] = backend.NS + "node." + "f" * 32
        with self.assertRaises(backend.HubError):
            backend.assign_entries(registry(first), [source()])


class PolicyContracts(unittest.TestCase):
    def test_nested_consistent_groups_classify_without_selector_mutation(self):
        base = {"proxies": [node()], "proxy-groups": [
            {"name": "internet", "type": "url-test", "proxies": ["美国中转815"]},
            {"name": "nested", "type": "fallback", "proxies": ["internet"]},
            {"name": "local", "type": "select", "proxies": ["DIRECT"]},
            {"name": "blocked", "type": "select", "proxies": ["REJECT"]}],
            "rules": ["DOMAIN,direct.example.invalid,local", "DOMAIN,blocked.example.invalid,blocked", "MATCH,nested"]}
        before = copy.deepcopy(base)
        policy, issues = backend.assess_policy(base)
        self.assertEqual(issues, [])
        self.assertEqual(policy, {"local": "DIRECT", "blocked": "REJECT", "nested": "PROXY"})
        self.assertEqual(base, before)

    def test_mixed_group_requires_explicit_review(self):
        base = {"proxies": [node()], "proxy-groups": [{"name": "mixed", "type": "select", "proxies": ["DIRECT", "美国中转815"]}], "rules": ["MATCH,mixed"]}
        policy, issues = backend.assess_policy(base)
        self.assertNotIn("mixed", policy)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["target"], "mixed")
        self.assertEqual(set(issues[0]["categories"]), {"DIRECT", "PROXY"})
        reviewed, pending = backend.assess_policy(base, {"mixed": "PROXY"})
        self.assertEqual(reviewed["mixed"], "PROXY")
        self.assertEqual(pending, [])

    def test_unknown_or_cyclic_group_is_not_guessed(self):
        for groups in ([], [{"name": "unknown", "type": "select", "proxies": ["unknown"]}]):
            policy, issues = backend.assess_policy({"proxies": [], "proxy-groups": groups, "rules": ["MATCH,unknown"]})
            self.assertNotIn("unknown", policy)
            self.assertEqual(len(issues), 1)

    def test_invalid_reviewed_action_is_rejected(self):
        with self.assertRaises(backend.HubError):
            backend.assess_policy({"rules": ["MATCH,mixed"]}, {"mixed": "choose-fastest"})


class RoutingContracts(unittest.TestCase):
    def test_transform_does_not_change_inputs(self):
        base, entries, policy = routing_bundle()
        before = copy.deepcopy((base, entries, policy))
        backend.convert_routing(base, entries, policy)
        self.assertEqual((base, entries, policy), before)

    def test_direct_and_block_rules_retain_priority_and_action(self):
        base, entries, policy = routing_bundle()
        result = backend.convert_routing(base, entries, policy)
        rules = result["sub-rules"][backend.NS + "rules"]
        self.assertEqual(rules[:2], ["DOMAIN,direct.example.invalid,DIRECT", "DOMAIN,blocked.example.invalid,REJECT"])

    def test_proxy_rules_dispatch_exact_node_with_reject_guard(self):
        base, entries, policy = routing_bundle()
        result = backend.convert_routing(base, entries, policy)
        self.assertEqual(result["proxy-groups"], [])
        self.assertEqual(result["rules"], ["MATCH,REJECT"])
        dispatch = result["sub-rules"][backend.NS + "dispatch"]
        for entry in entries:
            listener = next(listener for listener in result["listeners"] if listener["port"] == entry["port"])
            self.assertIn("IN-NAME," + listener["name"] + "," + entry["internalName"], dispatch)
        rules = result["sub-rules"][backend.NS + "rules"]
        self.assertIn("SUB-RULE,(DOMAIN-SUFFIX,proxy.example.invalid)," + backend.NS + "dispatch", rules)
        self.assertIn("DOMAIN-SUFFIX,proxy.example.invalid,REJECT", rules)
        self.assertIn("SUB-RULE,(IP-CIDR,203.0.113.0/24,no-resolve)," + backend.NS + "dispatch", rules)
        self.assertIn("IP-CIDR,203.0.113.0/24,REJECT,no-resolve", rules)
        self.assertEqual(dispatch[-1], "MATCH,REJECT")

    def test_listeners_are_only_tcp_loopback_and_use_rule(self):
        base, entries, policy = routing_bundle()
        result = backend.convert_routing(base, entries, policy)
        for listener in result["listeners"]:
            self.assertEqual(listener["listen"], "127.0.0.1")
            self.assertEqual(listener["type"], "socks")
            self.assertIs(listener["udp"], False)
            self.assertEqual(listener["rule"], backend.NS + "rules")
            self.assertNotIn("proxy", listener)

    def test_removed_and_missing_nodes_have_dead_listeners(self):
        base, entries, policy = routing_bundle()
        entries[0]["active"] = False
        base["proxies"] = []
        result = backend.convert_routing(base, entries, policy)
        for listener in result["listeners"]:
            self.assertEqual(listener["rule"], backend.NS + "dead")
        self.assertEqual(result["sub-rules"][backend.NS + "dead"], ["MATCH,REJECT"])
        self.assertTrue(all(rule.endswith(",REJECT") for rule in result["sub-rules"][backend.NS + "dispatch"]))

    def test_match_uses_fixed_proxy_even_if_source_template_match_is_direct(self):
        base, entries, policy = routing_bundle()
        base["rules"] = ["MATCH,DIRECT"]
        result = backend.convert_routing(base, entries, policy)
        rules = result["sub-rules"][backend.NS + "rules"]
        self.assertIn("SUB-RULE,(OR,((NETWORK,tcp),(NETWORK,udp)))," + backend.NS + "dispatch", rules)
        self.assertNotIn("MATCH,DIRECT", rules)
        self.assertEqual(rules[-1], "MATCH,REJECT")

    def test_match_reject_and_reject_drop_stay_blocked(self):
        for action in ("REJECT", "REJECT-DROP"):
            base, entries, policy = routing_bundle()
            base["rules"] = ["MATCH," + action]
            result = backend.convert_routing(base, entries, policy)
            self.assertEqual(result["sub-rules"][backend.NS + "rules"][0], "MATCH," + action)

    def test_nested_subrules_are_mapped_without_mutating_original(self):
        base, entries, policy = routing_bundle()
        base["rules"] = ["SUB-RULE,(NETWORK,tcp),nested", "MATCH,Other"]
        base["sub-rules"] = {"nested": ["DOMAIN,blocked.example.invalid,Block", "MATCH,Internet"]}
        result = backend.convert_routing(base, entries, policy)
        self.assertIn("DOMAIN,blocked.example.invalid,REJECT", result["sub-rules"][backend.NS + "nested.1"])
        self.assertEqual(base["sub-rules"]["nested"][0], "DOMAIN,blocked.example.invalid,Block")

    def test_regex_unknown_target_and_cycle_fail_closed(self):
        for rules, subrules in ((["DOMAIN-REGEX,^private.*invalid,Internet"], {}),
                                (["MATCH,unreviewed-group"], {}),
                                (["SUB-RULE,(NETWORK,tcp),cycle"], {"cycle": ["SUB-RULE,(NETWORK,tcp),cycle"]}),
                                (["AND,((DOMAIN,a.invalid),(NETWORK,tcp)),Internet)"], {})):
            base, entries, policy = routing_bundle()
            base.update(rules=rules, **{"sub-rules": subrules})
            with self.assertRaises(backend.HubError):
                backend.convert_routing(base, entries, policy)

    @unittest.skipUnless(os.environ.get("MSH_TEST_CORE"), "Set MSH_TEST_CORE for synthetic syntax validation only")
    def test_optional_actual_core_parses_dispatch_and_subrules(self):
        base, entries, policy = routing_bundle()
        config = backend.convert_routing(base, entries, policy)
        config.update({"mode": "rule", "allow-lan": False, "bind-address": "127.0.0.1", "tun": {"enable": False}, "dns": {"enable": False}})
        with TemporaryDirectory(prefix="msh-core-contract-") as temporary:
            directory = Path(temporary)
            candidate = directory / "candidate.yaml"
            candidate.write_bytes(backend.life.yaml_bytes(config))
            process = subprocess.run([os.environ["MSH_TEST_CORE"], "-t", "-d", str(directory), "-f", str(candidate)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(process.returncode, 0, "Mihomo rejected synthetic routing; raw diagnostics intentionally not exposed")


class PublicExportContracts(unittest.TestCase):
    def test_public_summary_and_errors_never_export_auth_fields(self):
        _, entries, _ = routing_bundle()
        entries[0].update(password="SYNTHETIC_SECRET_DO_NOT_EXPORT", url="https://synthetic.invalid/PRIVATE_URL", uuid="PRIVATE_UUID")
        public = backend.Backend._safe_entries(entries)
        serialized = json.dumps(public)
        for marker in ("SYNTHETIC_SECRET_DO_NOT_EXPORT", "PRIVATE_URL", "PRIVATE_UUID", "internalName"):
            self.assertNotIn(marker, serialized)

        @backend.safe_public
        def unexpected_failure():
            raise RuntimeError("SYNTHETIC_SECRET_DO_NOT_EXPORT")

        self.assertEqual(unexpected_failure(), {"ok": False, "code": "LOCAL_OPERATION_FAILED"})

    def test_export_contains_only_fixed_loopback_profiles_and_keeps_dead_entry(self):
        _, entries, _ = routing_bundle()
        entries[0]["active"] = False
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            with export_ready(hub, entries):
                result = hub.export_bak(root / "public")
            self.assertTrue(result["ok"], result.get("code"))
            exported = json.loads(Path(result["files"]["bak"]).read_text(encoding="utf8"))
            profiles = [profile for key, profile in exported.items() if key.startswith("+")]
            self.assertEqual(len(profiles), len(entries))
            for entry in entries:
                profile = exported["+" + entry["profileName"]]
                self.assertEqual(profile["profileType"], "FixedProfile")
                self.assertEqual(profile["fallbackProxy"], {"scheme": "socks5", "host": "127.0.0.1", "port": entry["port"]})
            public_bytes = Path(result["files"]["bak"]).read_bytes() + Path(result["files"]["mapping"]).read_bytes()
            for forbidden in (b"SYNTHETIC_SECRET_DO_NOT_EXPORT", b"subscription.example.invalid", b"192.0.2.10", b"AutoSwitch", b"PacProfile"):
                self.assertNotIn(forbidden, public_bytes)

    def test_existing_exports_are_backed_up_privately_before_replacement(self):
        _, entries, _ = routing_bundle()
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            private = root / "private"
            (private / "backups").mkdir(parents=True)
            public = root / "public"
            public.mkdir()
            (public / backend.BAK_NAME).write_bytes(b"synthetic-existing-bak")
            (public / "多订阅-节点与端口映射表.md").write_bytes(b"synthetic-existing-map")
            hub = backend.Backend(private, clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            with export_ready(hub, entries):
                result = hub.export_bak(public)
            self.assertTrue(result["ok"], result.get("code"))
            self.assertEqual({p.read_bytes() for p in (private / "backups").iterdir()}, {b"synthetic-existing-bak", b"synthetic-existing-map"})

    def test_export_into_private_directory_is_rejected(self):
        _, entries, _ = routing_bundle()
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            with export_ready(hub, entries):
                result = hub.export_bak(hub.root / "exports")
            self.assertEqual(result, {"ok": False, "code": "PUBLIC_EXPORT_MUST_BE_OUTSIDE_PRIVATE"})

    def test_backup_cannot_be_exported_before_latest_preparation_is_running(self):
        _, entries, _ = routing_bundle()
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            with patch.object(hub, "_ensure_root"), \
                 patch.object(hub, "_prepared_record", return_value=({"token": "a" * 32, "entries": entries}, None)), \
                 patch.object(hub, "_state", return_value=None):
                result = hub.export_bak(root / "public")
            self.assertEqual(result, {"ok": False, "code": "NOT_APPLIED"})
            self.assertFalse((root / "public").exists())

    def test_wrong_listener_owner_blocks_backup_export(self):
        _, entries, _ = routing_bundle()
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            with export_ready(hub, entries), patch.object(backend.life, "listener_rows", return_value=[]):
                result = hub.export_bak(root / "public")
            self.assertEqual(result, {"ok": False, "code": "LISTENER_IDENTITY_MISMATCH"})
            self.assertFalse((root / "public").exists())

    def test_old_live_token_cannot_export_new_prepared_backup(self):
        _, entries, _ = routing_bundle()
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            stale = {"status": "running", "token": "b" * 32, "pid": 1234, "entries": entries}
            with export_ready(hub, entries), patch.object(hub, "_state", return_value=stale):
                result = hub.export_bak(root / "public")
            self.assertEqual(result, {"ok": False, "code": "NOT_APPLIED"})
            self.assertFalse((root / "public").exists())

    def test_old_live_token_cannot_test_new_prepared_nodes(self):
        _, entries, _ = routing_bundle()
        with TemporaryDirectory(prefix="msh-export-contract-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            stale = {"status": "running", "token": "b" * 32, "pid": 1234, "entries": entries}
            with export_ready(hub, entries), patch.object(hub, "_state", return_value=stale), patch.object(hub, "_probe") as probe:
                result = hub.test_nodes()
            self.assertEqual(result, {"ok": False, "code": "NOT_APPLIED"})
            probe.assert_not_called()

    def test_connection_uri_in_label_is_redacted_in_profiles_and_public_rows(self):
        original = source(nodes=[node(name="vless://SYNTHETIC_URI_SECRET@edge.example.invalid:443")])
        entries = backend.assign_entries(registry(), [original])
        self.assertNotIn("SYNTHETIC_URI_SECRET", entries[0]["profileName"])
        self.assertNotIn("SYNTHETIC_URI_SECRET", json.dumps(backend.Backend._safe_entries(entries)))


class DependencyAndProcessContracts(unittest.TestCase):
    def test_bridge_dispatch_uses_bundled_lifecycle_without_old_exe_or_cmd(self):
        with TemporaryDirectory(prefix="msh-dependency-command-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", bridge_root=root / "fake-bridge" / "private")
            self.assertFalse((hub.bridge_root.parent / "FeiniaoClashBridge.exe").exists())
            for action in ("start", "stop", "status"):
                expected = {"ok": True, "running": action != "stop"}
                with self.subTest(action=action), \
                     patch.object(backend.life, action, return_value=expected) as lifecycle, \
                     patch.object(backend.subprocess, "run") as subprocess_run:
                    result = hub._dependency_command(action)
                self.assertEqual(result, expected)
                lifecycle.assert_called_once_with(hub.bridge_root)
                subprocess_run.assert_not_called()

    def test_bridge_dispatch_rejects_actions_outside_whitelist(self):
        with TemporaryDirectory(prefix="msh-dependency-command-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", bridge_root=root / "fake-bridge")
            with patch.object(backend.life, "start") as start, patch.object(backend.life, "stop") as stop, patch.object(backend.life, "status") as status:
                for action in ("reload", "install", "__dict__", "", None, True):
                    with self.subTest(action=action), self.assertRaises(backend.HubError) as caught:
                        hub._dependency_command(action)
                    self.assertEqual(caught.exception.code, "BRIDGE_ACTION_INVALID")
            start.assert_not_called()
            stop.assert_not_called()
            status.assert_not_called()

    def test_bridge_dispatch_rejects_false_or_non_dictionary_results(self):
        with TemporaryDirectory(prefix="msh-dependency-command-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", bridge_root=root / "fake-bridge")
            for response in (None, [], False, "SYNTHETIC_PRIVATE_ERROR", {}, {"ok": False, "error": "SYNTHETIC_PRIVATE_ERROR"}):
                with self.subTest(kind=type(response).__name__), patch.object(backend.life, "status", return_value=response):
                    with self.assertRaises(backend.HubError) as caught:
                        hub._dependency_command("status")
                self.assertEqual(caught.exception.code, "BRIDGE_COMMAND_FAILED")
                self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", str(caught.exception))

    def test_bridge_lifecycle_failure_is_public_only_as_stable_code(self):
        with TemporaryDirectory(prefix="msh-dependency-command-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", bridge_root=root / "fake-bridge")
            @backend.safe_public
            def call():
                return hub._dependency_command("status")
            with patch.object(backend.life, "status", side_effect=backend.life.BridgeError("PROCESS_IDENTITY_MISMATCH")):
                self.assertEqual(call(), {"ok": False, "code": "PROCESS_IDENTITY_MISMATCH"})

    def test_unexpected_bridge_lifecycle_error_does_not_echo_private_text(self):
        with TemporaryDirectory(prefix="msh-dependency-command-") as temporary:
            root = Path(temporary).resolve()
            hub = backend.Backend(root / "private", bridge_root=root / "fake-bridge")
            @backend.safe_public
            def call():
                return hub._dependency_command("status")
            with patch.object(backend.life, "status", side_effect=RuntimeError("SYNTHETIC_PRIVATE_AUTH_AND_PATH")):
                self.assertEqual(call(), {"ok": False, "code": "LOCAL_OPERATION_FAILED"})

    def test_stop_borrowed_bridge_never_queries_or_stops_it(self):
        with TemporaryDirectory(prefix="msh-dependency-contract-") as temporary:
            hub = backend.Backend(Path(temporary).resolve() / "private", bridge_root=Path(temporary).resolve() / "fake-bridge")
            with patch.object(hub, "_dependency_command") as command, patch.object(backend.life, "read_json") as read:
                hub._dependency_stop({"owned": False, "pid": 1234})
            command.assert_not_called()
            read.assert_not_called()

    def test_start_running_bridge_is_borrowed_without_start_command(self):
        with TemporaryDirectory(prefix="msh-dependency-contract-") as temporary:
            root = Path(temporary).resolve()
            bridge = root / "fake-bridge"
            bridge.mkdir()
            backend.life.atomic_json(bridge / "state.json", {"pid": 1234, "creation_time": "500", "exe_sha256": "synthetic-hash", "install_id": "synthetic-install"})
            hub = backend.Backend(root / "private", bridge_root=bridge)
            with patch.object(hub, "_dependency_command", return_value={"ok": True, "running": True, "identity_matches": True, "all_listeners_private": True}) as command:
                dependency = hub._dependency_start(True)
            self.assertFalse(dependency["owned"])
            command.assert_called_once_with("status")

    def test_owned_bridge_replacement_is_not_stopped(self):
        with TemporaryDirectory(prefix="msh-dependency-contract-") as temporary:
            root = Path(temporary).resolve()
            bridge = root / "fake-bridge"
            bridge.mkdir()
            backend.life.atomic_json(bridge / "state.json", {"pid": 1234, "creation_time": "501", "exe_sha256": "synthetic-hash", "install_id": "synthetic-install"})
            hub = backend.Backend(root / "private", bridge_root=bridge)
            with patch.object(hub, "_dependency_command") as command:
                hub._dependency_stop({"owned": True, "pid": 1234, "creation_time": "500", "exeHash": "synthetic-hash", "installId": "synthetic-install"})
            command.assert_not_called()

    def test_same_owned_bridge_instance_can_be_stopped(self):
        with TemporaryDirectory(prefix="msh-dependency-contract-") as temporary:
            root = Path(temporary).resolve()
            bridge = root / "fake-bridge"
            bridge.mkdir()
            backend.life.atomic_json(bridge / "state.json", {"pid": 1234, "creation_time": "500", "exe_sha256": "synthetic-hash", "install_id": "synthetic-install"})
            hub = backend.Backend(root / "private", bridge_root=bridge)
            with patch.object(hub, "_dependency_command") as command:
                hub._dependency_stop({"owned": True, "pid": 1234, "creation_time": "500", "exeHash": "synthetic-hash", "installId": "synthetic-install"})
            command.assert_called_once_with("stop")

    def test_process_identity_rejects_missing_creation_time_pid_reuse_and_other_exe(self):
        with TemporaryDirectory(prefix="msh-process-contract-") as temporary:
            root = Path(temporary).resolve()
            private = root / "private"
            (private / "bin").mkdir(parents=True)
            (private / "data").mkdir()
            (private / "bin/mihomo.exe").write_bytes(b"synthetic-executable-never-run")
            (private / "config.yaml").write_bytes(b"synthetic-config")
            hub = backend.Backend(private, clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            argv = [str(private / "bin/mihomo.exe"), "-d", str(private / "data"), "-f", str(private / "config.yaml")]
            state = {"pid": 1234, "creation_time": "500", "installId": "synthetic-install", "exeHash": backend.life.file_hash(private / "bin/mihomo.exe"), "configHash": backend.life.file_hash(private / "config.yaml")}
            actual = {"pid": 1234, "creation_time": "500", "exe": str(private / "bin/mihomo.exe"), "argv": argv}
            with patch.object(hub, "_ensure_root", return_value={"installId": "synthetic-install"}):
                self.assertTrue(hub._identity(state, actual))
                for altered in ({"creation_time": "501"}, {"exe": str(root / "main-core.exe")}, {"pid": 5678}):
                    self.assertFalse(hub._identity(state, {**actual, **altered}))
                self.assertFalse(hub._identity({**state, "creation_time": None}, {**actual, "creation_time": None}))

    def test_safe_public_bridge_error_returns_only_stable_code(self):
        @backend.safe_public
        def failure():
            raise backend.HubError("PROCESS_IDENTITY_MISMATCH")

        self.assertEqual(failure(), {"ok": False, "code": "PROCESS_IDENTITY_MISMATCH"})

    def test_failed_update_restores_owned_bridge_and_leaves_borrowed_bridge_alone(self):
        for owned in (True, False):
            with self.subTest(owned=owned), TemporaryDirectory(prefix="msh-rollback-contract-") as temporary:
                root = Path(temporary).resolve()
                private = root / "private"
                (private / "backups").mkdir(parents=True)
                config = private / "config.yaml"
                config.write_bytes(b"synthetic-old-config")
                candidate = root / "synthetic-candidate.yaml"
                candidate.write_bytes(b"synthetic-new-config")
                _, entries, _ = routing_bundle()
                dependency = {"owned": owned, "pid": 4321, "creation_time": "500", "exeHash": "synthetic-old-hash", "installId": "synthetic-install"}
                restored_dependency = {**dependency, "pid": 4322, "creation_time": "501"} if owned else copy.deepcopy(dependency)
                old_state = {"status": "running", "pid": 1234, "creation_time": "500", "configHash": "old-hash", "dependency": dependency,
                             "entries": entries, "token": "a" * 32}
                old_record = {"entries": entries, "token": "a" * 32, "dependency": True, "selected": [entry["sourceId"] for entry in entries]}
                new_record = {**old_record, "configHash": "new-hash", "token": "b" * 32, "dependency": False}
                backend.life.atomic_json(private / "applied.json", old_record)
                hub = backend.Backend(private, clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
                with patch.object(hub, "_ensure_root", return_value={"installId": "synthetic-install"}), \
                     patch.object(hub, "_prepared_record", return_value=(new_record, candidate)), \
                     patch.object(backend.life, "operation_lock", return_value=nullcontext()), \
                     patch.object(hub, "_validate"), patch.object(hub, "_state", return_value=old_state), \
                     patch.object(hub, "_actual", return_value={"pid": 1234, "creation_time": "500"}), \
                     patch.object(hub, "_identity", return_value=True), patch.object(hub, "_stop_own") as stop, \
                     patch.object(hub, "_dependency_stop") as stop_dependency, \
                     patch.object(hub, "_dependency_start", return_value=restored_dependency) as start_dependency, \
                     patch.object(hub, "_launch", side_effect=[backend.HubError("CORE_START_FAILED"), {"pid": 9999}]) as launch:
                    result = hub.start()
                self.assertEqual(result, {"ok": False, "code": "CORE_START_FAILED"})
                self.assertEqual(config.read_bytes(), b"synthetic-old-config")
                stop.assert_called_once_with(old_state, stop_dependency=False)
                stop_dependency.assert_called_once_with(dependency)
                # A borrowed live bridge is rechecked through status only; the
                # lower-level running-bridge test proves that does not start it.
                start_dependency.assert_called_once_with(True)
                self.assertEqual(launch.call_args_list[-1].args, (old_record, restored_dependency))


class RuntimeStatusContracts(unittest.TestCase):
    def _status(self, temporary, *, running=True, token_matches=True, healthy=True, pending_registry=None, borrowed=True):
        root = Path(temporary).resolve()
        private = root / "private"
        private.mkdir()
        _, live, _ = routing_bundle()
        backend.life.atomic_json(private / "registry.json", registry(pending_registry if pending_registry is not None else live))
        backend.life.atomic_json(private / "last-prepared.json", {"token": "a" * 32})
        state = {"status": "running" if running else "stopped", "pid": 1234, "creation_time": "500", "token": ("a" if token_matches else "b") * 32,
                 "entries": live, "selected": [entry["sourceId"] for entry in live], "dependency": {"owned": not borrowed}}
        rows = [{"pid": 1234, "address": "127.0.0.1", "port": entry["port"]} for entry in live] if healthy else []
        hub = backend.Backend(private, clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
        with patch.object(hub, "_ensure_root"), patch.object(hub, "_state", return_value=state), \
             patch.object(hub, "_actual", return_value={"pid": 1234, "creation_time": "500"}), \
             patch.object(hub, "_identity", return_value=True), patch.object(backend.life, "listener_rows", return_value=rows):
            return hub.status(), live

    def test_current_live_token_has_no_pending_change_and_borrowed_bridge_is_not_owned(self):
        with TemporaryDirectory(prefix="msh-status-contract-") as temporary:
            result, live = self._status(temporary)
        self.assertTrue(result["ok"], result.get("code"))
        self.assertTrue(result["running"])
        self.assertTrue(result["allListenersPrivate"])
        self.assertFalse(result["pendingChanges"])
        self.assertFalse(result["bridgeOwned"])
        self.assertEqual(result["sourceIds"], [entry["sourceId"] for entry in live])

    def test_prepared_registry_and_running_entries_are_distinguished(self):
        _, pending, _ = routing_bundle()
        pending[0]["active"] = False
        with TemporaryDirectory(prefix="msh-status-contract-") as temporary:
            result, _ = self._status(temporary, token_matches=False, pending_registry=pending)
        self.assertTrue(result["pendingChanges"])
        self.assertFalse(result["entries"][0]["active"])
        self.assertTrue(result["liveEntries"][0]["active"])

    def test_stopped_core_reports_pending_without_any_live_entries(self):
        with TemporaryDirectory(prefix="msh-status-contract-") as temporary:
            result, _ = self._status(temporary, running=False)
        self.assertFalse(result["running"])
        self.assertTrue(result["pendingChanges"])
        self.assertEqual(result["liveEntries"], [])
        self.assertEqual(result["sourceIds"], [])

    def test_incomplete_listener_set_is_not_reported_healthy(self):
        with TemporaryDirectory(prefix="msh-status-contract-") as temporary:
            result, _ = self._status(temporary, healthy=False)
        self.assertTrue(result["running"])
        self.assertFalse(result["allListenersPrivate"])


class PreparationContracts(unittest.TestCase):
    def test_prepared_record_accepts_only_unique_source_hashes_and_boolean_dependency(self):
        with TemporaryDirectory(prefix="msh-prepared-contract-") as temporary:
            root = Path(temporary).resolve()
            private = root / "private"
            token = "a" * 32
            directory = private / "prepared" / token
            directory.mkdir(parents=True)
            candidate = directory / "config.yaml"
            candidate.write_bytes(b"synthetic-config-never-run")
            record = {"token": token, "configHash": backend.life.file_hash(candidate), "selected": [source()["id"]], "dependency": False}
            backend.life.atomic_json(directory / "record.json", record)
            hub = backend.Backend(private, clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            actual, path = hub._prepared_record(token)
            self.assertEqual(actual, record)
            self.assertEqual(path, candidate)
            for changed in ({"selected": []}, {"selected": "a" * 64}, {"selected": ["source-id"]},
                            {"selected": [source()["id"], source()["id"]]}, {"selected": [123]},
                            {"dependency": 1}, {"dependency": "true"}):
                with self.subTest(field=next(iter(changed))):
                    backend.life.atomic_json(directory / "record.json", {**record, **changed})
                    with self.assertRaises(backend.HubError) as caught:
                        hub._prepared_record(token)
                    self.assertEqual(caught.exception.code, "PREPARED_INVALID")

            backend.life.atomic_json(directory / "record.json", {**record, "selected": [[]]})
            @backend.safe_public
            def malformed_unhashable_record():
                hub._prepared_record(token)
                return {"ok": True}
            # A malformed unhashable ID fails before process/file mutation;
            # the public wrapper exposes no record content or traceback.
            self.assertEqual(malformed_unhashable_record(), {"ok": False, "code": "LOCAL_OPERATION_FAILED"})

    def test_prepared_record_detects_config_tampering_and_invalid_token(self):
        with TemporaryDirectory(prefix="msh-prepared-contract-") as temporary:
            root = Path(temporary).resolve()
            private = root / "private"
            token = "a" * 32
            directory = private / "prepared" / token
            directory.mkdir(parents=True)
            candidate = directory / "config.yaml"
            candidate.write_bytes(b"synthetic-original-config")
            record = {"token": token, "configHash": backend.life.file_hash(candidate), "selected": [source()["id"]], "dependency": False}
            backend.life.atomic_json(directory / "record.json", record)
            candidate.write_bytes(b"synthetic-tampered-config")
            hub = backend.Backend(private, clash_dir=root / "fake-clash", bridge_root=root / "fake-bridge")
            with self.assertRaises(backend.HubError) as changed:
                hub._prepared_record(token)
            self.assertEqual(changed.exception.code, "PREPARED_CHANGED")
            with self.assertRaises(backend.HubError) as unsafe:
                hub._prepared_record("../../other")
            self.assertEqual(unsafe.exception.code, "NOT_PREPARED")

    def test_prepare_is_readonly_to_clash_and_never_launches_core(self):
        with TemporaryDirectory(prefix="msh-prepare-contract-") as temporary:
            root = Path(temporary).resolve()
            clash = root / "fake-clash"
            (clash / "profiles").mkdir(parents=True)
            items = [
                {"uid": "source-a", "type": "remote", "name": "订阅一", "file": "a.yaml", "url": "https://subscription.example.invalid/a"},
                {"uid": "source-b", "type": "remote", "name": "订阅二", "file": "b.yaml", "url": "https://subscription.example.invalid/b"},
            ]
            for item, server in zip(items, ("192.0.2.10", "198.51.100.22")):
                (clash / "profiles" / item["file"]).write_bytes(backend.life.yaml_bytes({"proxies": [node(server=server)], "proxy-groups": [], "rules": ["MATCH,美国中转815"]}))
            (clash / "profiles.yaml").write_bytes(backend.life.yaml_bytes({"current": "source-a", "items": items}))
            runtime = {"proxies": [node()], "proxy-groups": [],
                       "rules": ["DOMAIN,direct.example.invalid,DIRECT", "DOMAIN,blocked.example.invalid,REJECT", "MATCH,美国中转815"],
                       "mode": "rule", "mixed-port": 7890, "external-controller": "127.0.0.1:9090", "secret": "SYNTHETIC_MAIN_SECRET",
                       "listeners": [{"name": "czo.v1.in.synthetic", "type": "socks", "listen": "127.0.0.1", "port": 20000}],
                       "tun": {"enable": True}, "dns": {"enable": True, "listen": "127.0.0.1:1053", "nameserver": ["1.1.1.1"]}}
            (clash / "clash-verge.yaml").write_bytes(backend.life.yaml_bytes(runtime))
            original = {p.relative_to(clash): p.read_bytes() for p in clash.rglob("*") if p.is_file()}
            inert_core = root / "synthetic-core.exe"
            inert_core.write_bytes(b"synthetic-executable-never-run")
            hub = backend.Backend(root / "private", clash_dir=clash, core_path=inert_core, bridge_root=root / "fake-bridge")
            with patch.object(hub, "_bridge_mapping", return_value={}), \
                 patch.object(hub, "_reviewed_policy", return_value={}), \
                 patch.object(backend.life, "protect_directory"), patch.object(backend.life, "verify_acl"), \
                 patch.object(backend, "excluded_ports", return_value={46000}), \
                 patch.object(backend, "_port_available", side_effect=lambda port: port != 46001), \
                 patch.object(hub, "_validate") as validate, \
                 patch.object(backend.subprocess, "Popen") as launch, patch.object(backend, "api") as api:
                result = hub.prepare([backend.source_identity(item) for item in items])
            self.assertTrue(result["ok"], result.get("code"))
            self.assertTrue(result["prepared"])
            self.assertEqual([entry["port"] for entry in result["entries"]], [46002, 46003])
            candidate = backend.life.read_yaml(validate.call_args.args[0])
            self.assertEqual(candidate["tun"], {"enable": False})
            self.assertNotIn("listen", candidate["dns"])
            self.assertNotIn("mixed-port", candidate)
            self.assertNotIn("external-controller", candidate)
            self.assertTrue(candidate["external-controller-pipe"].startswith(r"\\.\pipe\codex-msh-"))
            self.assertNotEqual(candidate["secret"], runtime["secret"])
            self.assertEqual(candidate["dns"]["nameserver"], runtime["dns"]["nameserver"])
            self.assertTrue(all(listener["port"] >= 46000 and listener["listen"] == "127.0.0.1" for listener in candidate["listeners"]))
            self.assertEqual({p.relative_to(clash): p.read_bytes() for p in clash.rglob("*") if p.is_file()}, original)
            launch.assert_not_called()
            api.assert_not_called()


class CoreCopyContracts(unittest.TestCase):
    def _fixture(self, temporary):
        root = Path(temporary).resolve()
        private = root / "private"
        (private / "bin").mkdir(parents=True)
        (private / "data").mkdir()
        executable = root / "synthetic-source-core.exe"
        executable.write_bytes(b"synthetic-executable-never-run")
        hub = backend.Backend(private, clash_dir=root / "fake-clash", core_path=executable, bridge_root=root / "fake-bridge")
        return hub, executable, private / "bin/mihomo.exe"

    def test_first_install_verifies_temp_copy_then_publishes_atomically(self):
        with TemporaryDirectory(prefix="msh-core-copy-contract-") as temporary:
            hub, source_core, destination = self._fixture(temporary)
            real_replace = backend.os.replace
            with patch.object(backend.os, "replace", wraps=real_replace) as replace:
                result = hub._install_core()
            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), source_core.read_bytes())
            src, dst = replace.call_args.args
            self.assertEqual(src.parent, destination.parent)
            self.assertTrue(src.name.startswith("core-copy-"))
            self.assertEqual(dst, destination)
            self.assertFalse(list(destination.parent.glob("*.tmp")))

    def test_different_installed_core_is_not_overwritten(self):
        with TemporaryDirectory(prefix="msh-core-copy-contract-") as temporary:
            hub, _, destination = self._fixture(temporary)
            destination.write_bytes(b"synthetic-existing-core-keep")
            with patch.object(backend.shutil, "copyfile") as copyfile:
                with self.assertRaises(backend.HubError) as caught:
                    hub._install_core()
            self.assertEqual(caught.exception.code, "CORE_UPDATE_REQUIRES_EXPLICIT_ACTION")
            self.assertEqual(destination.read_bytes(), b"synthetic-existing-core-keep")
            copyfile.assert_not_called()

    def test_unreadable_source_or_destination_has_non_sensitive_error(self):
        for unreadable in ("source", "destination"):
            with self.subTest(file=unreadable), TemporaryDirectory(prefix="msh-core-copy-contract-") as temporary:
                hub, source_core, destination = self._fixture(temporary)
                destination.write_bytes(source_core.read_bytes())
                target = source_core if unreadable == "source" else destination
                original_hash = backend.life.file_hash
                def hash_checked(path):
                    if path == target:
                        raise PermissionError("SYNTHETIC_PRIVATE_PATH_OR_AUTH")
                    return original_hash(path)
                with patch.object(backend.life, "file_hash", side_effect=hash_checked):
                    with self.assertRaises(backend.HubError) as caught:
                        hub._install_core()
                self.assertEqual(caught.exception.code, "CORE_FILE_UNREADABLE")
                self.assertNotIn("SYNTHETIC_PRIVATE_PATH_OR_AUTH", str(caught.exception))

    def test_copy_permission_failure_leaves_destination_absent(self):
        with TemporaryDirectory(prefix="msh-core-copy-contract-") as temporary:
            hub, _, destination = self._fixture(temporary)
            with patch.object(backend.shutil, "copyfile", side_effect=PermissionError("SYNTHETIC_PRIVATE_ERROR")):
                with self.assertRaises(backend.HubError) as caught:
                    hub._install_core()
            self.assertEqual(caught.exception.code, "CORE_COPY_FAILED")
            self.assertFalse(destination.exists())
            self.assertFalse(list(destination.parent.glob("*.tmp")))

    def test_changed_copy_is_rejected_and_cleaned_before_publish(self):
        with TemporaryDirectory(prefix="msh-core-copy-contract-") as temporary:
            hub, _, destination = self._fixture(temporary)
            with patch.object(backend.shutil, "copyfile", side_effect=lambda src, dst: dst.write_bytes(b"synthetic-corrupt-copy")):
                with self.assertRaises(backend.HubError) as caught:
                    hub._install_core()
            self.assertEqual(caught.exception.code, "CORE_COPY_CHANGED")
            self.assertFalse(destination.exists())
            self.assertFalse(list(destination.parent.glob("*.tmp")))

    def test_destination_created_during_copy_is_preserved(self):
        with TemporaryDirectory(prefix="msh-core-copy-contract-") as temporary:
            hub, _, destination = self._fixture(temporary)
            def competing_copy(source_core, temporary):
                temporary.write_bytes(source_core.read_bytes())
                destination.write_bytes(b"synthetic-other-writer-keep")
            with patch.object(backend.shutil, "copyfile", side_effect=competing_copy):
                with self.assertRaises(backend.HubError) as caught:
                    hub._install_core()
            self.assertEqual(caught.exception.code, "CORE_COPY_RACE")
            self.assertEqual(destination.read_bytes(), b"synthetic-other-writer-keep")
            self.assertFalse(list(destination.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
