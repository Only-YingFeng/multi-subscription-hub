"""Synthetic lifecycle/security checks; no user config or real core is touched."""
import copy
import hashlib
import importlib.util
import json
import os
import socket
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch


sys.dont_write_bytecode = True
MODULE = Path(__file__).resolve().parent.parent / "feiniao_bridge.py"
spec = importlib.util.spec_from_file_location("feiniao_bridge_under_test", MODULE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def bundle(count=2):
    nodes, listeners, entries, local, rules = [], [], [], [], []
    for index in range(count):
        name, port = f"synthetic-{index}", 31000 + index
        nodes.append({"name": name, "type": "vless", "server": "192.0.2.10", "port": 443,
                      "uid": 7, "version": 255, "uuid": "synthetic-auth", "tls": True,
                      "reality-opts": {"public-key": "synthetic-key", "short-id": "7e123456"}})
        listeners.append({"name": f"input-{index}", "type": "socks", "listen": "127.0.0.1", "port": port, "udp": False})
        entries.append({"identity": hashlib.sha256(name.encode()).hexdigest(), "nodeName": name, "port": port})
        local.append({"name": name, "type": "socks5", "server": "127.0.0.1", "port": port, "udp": False})
        rules.append(f"IN-PORT,{port},{name}")
    config = {"allow-lan": False, "bind-address": "127.0.0.1", "mode": "rule", "tun": {"enable": False},
              "dns": {"enable": False}, "proxies": nodes, "listeners": listeners, "rules": rules + ["MATCH,REJECT"]}
    return config, {"proxies": local, "proxy-groups": [], "rules": ["MATCH,synthetic-0"]}, {"version": 1, "entries": entries}


def create_stage(root):
    stage = root / "stage"
    stage.mkdir()
    config, imported, mapping = bundle(bridge.EXPECTED_COUNT)
    (stage / bridge.STAGE_CONFIG).write_bytes(bridge.yaml_bytes(config))
    (stage / bridge.STAGE_IMPORT).write_bytes(bridge.yaml_bytes(imported))
    bridge.atomic_json(stage / bridge.STAGE_MAP, mapping)
    core = stage / bridge.STAGE_CORE
    core.parent.mkdir(parents=True)
    core.write_bytes(b"synthetic-executable-never-run")
    (stage / "validation-data").mkdir()
    (stage / "validation-data" / "Country.mmdb").write_bytes(b"synthetic-public-data")
    (stage / "validation-data" / "cache.db").write_bytes(b"must-not-copy-cache")
    preflight = {"nodeCount": bridge.EXPECTED_COUNT, "bridgeValidationExitCode": 0, "importValidationExitCode": 0}
    for key in ("uniquePorts", "rulesRetainedExactly", "originalGroupTopologyRetained", "onlyLocalAddressesInClashNodes", "bridgeTunDisabled", "bridgeDnsListenerAbsent", "bridgeControlApiAbsent", "bridgeDefaultReject", "bridgeStillPrivate"):
        preflight[key] = True
    bridge.atomic_json(stage / bridge.STAGE_PREFLIGHT, preflight)
    return stage, config, imported, mapping


def lifecycle_state(root):
    """Create inert files and process identities for lifecycle decisions."""
    (root / "data").mkdir()
    (root / "logs").mkdir()
    (root / "core.exe").write_bytes(b"synthetic-executable-never-run")
    (root / "config.yaml").write_bytes(b"synthetic-private-auth")
    manifest = {"install_id": "a" * 32, "node_count": 2, "ports": [31000, 31001],
                "files": {name: bridge.file_hash(root / name) for name in ("core.exe", "config.yaml")}}
    state = {"status": "running", "pid": 1234, "creation_time": "500", "exe": str(root / "core.exe"),
             "config": str(root / "config.yaml"), "exe_sha256": manifest["files"]["core.exe"],
             "config_sha256": manifest["files"]["config.yaml"], "install_id": manifest["install_id"]}
    actual = {"pid": 1234, "creation_time": "500", "exe": str(root / "core.exe"),
              "argv": [str(root / "core.exe"), "-d", str(root / "data"), "-f", str(root / "config.yaml")]}
    return manifest, state, actual


class ValidationTests(unittest.TestCase):
    def test_preserves_shared_vendor_auth_and_scientific_looking_short_id(self):
        config, imported, mapping = bundle()
        before = copy.deepcopy(config)
        self.assertEqual(bridge.validate_bundle(config, imported, mapping, expected_count=2), [31000, 31001])
        self.assertEqual(config, before)
        serialized = bridge.yaml_bytes(config)
        self.assertIn(b'"7e123456"', serialized)
        self.assertIn(b'"uid": 7', serialized)
        self.assertIn(b'"version": 255', serialized)

    def assert_invalid(self, mutate):
        config, imported, mapping = bundle()
        mutate(config, imported, mapping)
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_bundle(config, imported, mapping, expected_count=2)

    def test_wrong_inport_and_direct_fallback_rejected(self):
        self.assert_invalid(lambda c, i, m: c["rules"].__setitem__(0, "IN-PORT,31000,synthetic-1"))
        self.assert_invalid(lambda c, i, m: c["rules"].__setitem__(-1, "MATCH,DIRECT"))

    def test_public_or_extra_listeners_rejected(self):
        self.assert_invalid(lambda c, i, m: c["listeners"][0].update(listen="0.0.0.0"))
        self.assert_invalid(lambda c, i, m: c["listeners"][0].update(proxy="DIRECT"))
        self.assert_invalid(lambda c, i, m: c.update({"external-controller": "127.0.0.1:9090"}))
        self.assert_invalid(lambda c, i, m: c["dns"].update(listen="127.0.0.1:1053"))
        self.assert_invalid(lambda c, i, m: c["tun"].update(enable=True))

    def test_duplicate_names_ports_and_identities_rejected(self):
        self.assert_invalid(lambda c, i, m: c["proxies"][1].update(name="synthetic-0"))
        self.assert_invalid(lambda c, i, m: m["entries"][1].update(port=31000))
        self.assert_invalid(lambda c, i, m: m["entries"][1].update(identity=m["entries"][0]["identity"]))
        self.assert_invalid(lambda c, i, m: m["entries"][0].update(port=True))

    def test_local_import_cannot_escape_or_enable_udp(self):
        self.assert_invalid(lambda c, i, m: i["proxies"][0].update(server="192.0.2.10"))
        self.assert_invalid(lambda c, i, m: i["proxies"][0].update(udp=True))
        self.assert_invalid(lambda c, i, m: i["proxies"][0].update(password="unexpected"))

    def test_vendor_uid_is_not_used_as_identity(self):
        config, imported, mapping = bundle()
        self.assertEqual(len({n["uid"] for n in config["proxies"]}), 1)
        self.assertEqual(len(bridge.validate_bundle(config, imported, mapping, expected_count=2)), 2)


class PathAndIdentityTests(unittest.TestCase):
    def test_atomic_json_uses_one_canonical_parent_for_source_and_target(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            (root / "alias").mkdir()
            target = root / "alias" / ".." / "state.json"
            original_replace = bridge.os.replace
            with patch.object(bridge.os, "replace", wraps=original_replace) as replace:
                bridge.atomic_json(target, {"synthetic": True})
            source, destination = replace.call_args.args
            self.assertEqual(source.parent, root.resolve())
            self.assertEqual(destination.parent, root.resolve())
            self.assertEqual(bridge.read_json(root / "state.json"), {"synthetic": True})

    @unittest.skipUnless(os.name == "nt", "Windows TCP listener query")
    def test_native_listener_query_for_own_temporary_loopback_socket(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            rows = bridge.listener_rows(os.getpid(), [port])
            self.assertTrue(bridge.listeners_match(rows, os.getpid(), [port]))

    @unittest.skipUnless(os.name == "nt", "Windows process handle")
    def test_native_handle_snapshot_and_stop_only_own_benign_child(self):
        argv = [str(Path(sys._base_executable).resolve()), "-c", "import time; time.sleep(60)"]
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with bridge.ProcessHandle(child.pid, terminate=True) as handle:
                actual = handle.snapshot()
                self.assertEqual(actual["pid"], child.pid)
                self.assertTrue(actual["creation_time"].isdigit())
                self.assertEqual(actual["argv"], argv)
                self.assertEqual(Path(actual["exe"]).resolve(), Path(argv[0]))
                handle.stop()
            self.assertIsNotNone(child.wait(timeout=5))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_traversal_absolute_and_ntfs_stream_rejected(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            for path in ("../other", "data/../../other", "core.exe:stream", str(root / "absolute")):
                with self.subTest(path=path), self.assertRaises(bridge.BridgeError):
                    bridge.safe_child(root, path)
            self.assertEqual(bridge.safe_child(root, "data/core.exe"), root / "data/core.exe")

    def test_junction_boundary_rejected(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            folder = root / "data"
            folder.mkdir()
            original = bridge._is_reparse
            with patch.object(bridge, "_is_reparse", side_effect=lambda p: p == folder or original(p)):
                with self.assertRaises(bridge.BridgeError):
                    bridge.safe_child(root, "data/config.yaml")

    def test_pid_reuse_exe_config_and_auth_hash_mismatches_rejected(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / "core.exe").write_bytes(b"never-run")
            (root / "config.yaml").write_bytes(b"synthetic-private-auth")
            files = {"core.exe": bridge.file_hash(root / "core.exe"), "config.yaml": bridge.file_hash(root / "config.yaml")}
            manifest = {"install_id": "a" * 32, "files": files}
            state = {"pid": 1234, "creation_time": "500", "exe": str(root / "core.exe"), "config": str(root / "config.yaml"),
                     "exe_sha256": files["core.exe"], "config_sha256": files["config.yaml"], "install_id": "a" * 32}
            actual = {"pid": 1234, "creation_time": "500", "exe": str(root / "core.exe"),
                      "argv": [str(root / "core.exe"), "-d", str(root / "data"), "-f", str(root / "config.yaml")]}
            self.assertTrue(bridge.identity_matches(state, actual, root, manifest))
            for replacement in ({"creation_time": "501"}, {"pid": 1235}, {"exe": str(root / "airport.exe")}, {"argv": actual["argv"][:-1] + [str(root / "other.yaml")]}):
                changed = {**actual, **replacement}
                self.assertFalse(bridge.identity_matches(state, changed, root, manifest))
            self.assertFalse(bridge.identity_matches({**state, "creation_time": None}, {**actual, "creation_time": None}, root, manifest))
            (root / "config.yaml").write_bytes(b"external-edit")
            self.assertFalse(bridge.identity_matches(state, actual, root, manifest))

    def test_listener_owner_and_address_must_match_all_ports(self):
        rows = [{"pid": 4, "address": "127.0.0.1", "port": 31000}, {"pid": 4, "address": "127.0.0.1", "port": 31001}]
        self.assertTrue(bridge.listeners_match(rows, 4, [31000, 31001]))
        self.assertFalse(bridge.listeners_match(rows[:-1], 4, [31000, 31001]))
        self.assertFalse(bridge.listeners_match([{**rows[0], "pid": 5}, rows[1]], 4, [31000, 31001]))
        self.assertFalse(bridge.listeners_match([{**rows[0], "address": "0.0.0.0"}, rows[1]], 4, [31000, 31001]))
        self.assertFalse(bridge.listeners_match(rows + [{"pid": 4, "address": "127.0.0.1", "port": 9090}], 4, [31000, 31001]))


class InstallationTests(unittest.TestCase):
    def test_stop_identity_mismatch_never_terminates_process(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.snapshot.return_value = {"creation_time": "500"}
            with patch.object(bridge, "_load_owned", return_value={}), \
                 patch.object(bridge, "_state", return_value={"pid": 1234, "status": "running", "creation_time": "500"}), \
                 patch.object(bridge, "ProcessHandle", return_value=handle), \
                 patch.object(bridge, "identity_matches", return_value=False):
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.stop(root)
                self.assertEqual(caught.exception.code, "PROCESS_IDENTITY_MISMATCH")
                handle.stop.assert_not_called()

    def test_start_after_pid_reuse_checks_ports_and_launches_new_private_instance(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            manifest, state, actual = lifecycle_state(root)
            reused = {**actual, "creation_time": "501", "exe": str(root / "other.exe"), "argv": []}
            fresh = {**actual, "pid": 2222, "creation_time": "600"}
            child = MagicMock(pid=2222)
            child.poll.return_value = None
            rows = [{"pid": 2222, "address": "127.0.0.1", "port": port} for port in manifest["ports"]]
            with patch.object(bridge, "_load_owned", return_value=manifest), \
                 patch.object(bridge, "_state", return_value=state), \
                 patch.object(bridge, "_actual", side_effect=[reused, fresh]), \
                 patch.object(bridge, "listener_rows", side_effect=[[], rows]) as query, \
                 patch.object(bridge.socket, "socket"), \
                 patch.object(bridge, "_validate_core"), \
                 patch.object(bridge.subprocess, "Popen", return_value=child) as launch:
                result = bridge.start(root)
            self.assertTrue(result["running"])
            self.assertFalse(result["unchanged"])
            self.assertEqual(query.call_args_list[0].args, (0, manifest["ports"]))
            self.assertEqual(launch.call_args.args[0], fresh["argv"])
            saved = bridge.read_json(root / "state.json")
            self.assertEqual((saved["pid"], saved["creation_time"]), (2222, "600"))
            child.terminate.assert_not_called()

    def test_start_pid_reuse_with_occupied_port_never_launches(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            manifest, state, actual = lifecycle_state(root)
            occupied = [{"pid": 5678, "address": "127.0.0.1", "port": 31000}]
            with patch.object(bridge, "_load_owned", return_value=manifest), \
                 patch.object(bridge, "_state", return_value=state), \
                 patch.object(bridge, "_actual", return_value={**actual, "creation_time": "501"}), \
                 patch.object(bridge, "listener_rows", return_value=occupied), \
                 patch.object(bridge.subprocess, "Popen") as launch:
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.start(root)
            self.assertEqual(caught.exception.code, "PORT_OCCUPIED")
            launch.assert_not_called()
            self.assertFalse((root / "state.json").exists())

    def test_start_same_instance_with_changed_identity_is_rejected(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            manifest, state, actual = lifecycle_state(root)
            with patch.object(bridge, "_load_owned", return_value=manifest), \
                 patch.object(bridge, "_state", return_value=state), \
                 patch.object(bridge, "_actual", return_value={**actual, "exe": str(root / "other.exe")}), \
                 patch.object(bridge, "listener_rows") as query, \
                 patch.object(bridge.subprocess, "Popen") as launch:
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.start(root)
            self.assertEqual(caught.exception.code, "PROCESS_IDENTITY_MISMATCH")
            query.assert_not_called()
            launch.assert_not_called()

    def test_stop_reused_pid_only_marks_old_instance_stopped_when_ports_are_free(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            manifest, state, actual = lifecycle_state(root)
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.snapshot.return_value = {**actual, "creation_time": "501", "exe": str(root / "other.exe"), "argv": []}
            with patch.object(bridge, "_load_owned", return_value=manifest), \
                 patch.object(bridge, "_state", return_value=state), \
                 patch.object(bridge, "ProcessHandle", return_value=handle), \
                 patch.object(bridge, "listener_rows", return_value=[]) as query:
                result = bridge.stop(root)
            self.assertFalse(result["running"])
            handle.stop.assert_not_called()
            query.assert_called_once_with(0, manifest["ports"])
            self.assertEqual(bridge.read_json(root / "state.json")["status"], "stopped")

    def test_stop_reused_pid_rejects_external_port_owner_without_termination(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            manifest, state, actual = lifecycle_state(root)
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.snapshot.return_value = {**actual, "creation_time": "501"}
            with patch.object(bridge, "_load_owned", return_value=manifest), \
                 patch.object(bridge, "_state", return_value=state), \
                 patch.object(bridge, "ProcessHandle", return_value=handle), \
                 patch.object(bridge, "listener_rows", return_value=[{"pid": 5678, "address": "127.0.0.1", "port": 31000}]):
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.stop(root)
            self.assertEqual(caught.exception.code, "PORT_OCCUPIED")
            handle.stop.assert_not_called()
            self.assertFalse((root / "state.json").exists())

    def test_stop_absent_instance_marks_stopped_only_after_free_port_check(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            manifest, state, _ = lifecycle_state(root)
            with patch.object(bridge, "_load_owned", return_value=manifest), \
                 patch.object(bridge, "_state", return_value=state), \
                 patch.object(bridge, "ProcessHandle", side_effect=bridge.BridgeError("PROCESS_NOT_FOUND")), \
                 patch.object(bridge, "listener_rows", return_value=[]) as query:
                result = bridge.stop(root)
            self.assertFalse(result["running"])
            query.assert_called_once_with(0, manifest["ports"])

    def test_missing_creation_time_is_not_accepted_as_proof_of_pid_reuse(self):
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.previous_instance_ended({"creation_time": None}, {"creation_time": "501"})
        self.assertEqual(caught.exception.code, "STATE_INVALID")
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.previous_instance_ended({"creation_time": "500"}, {"creation_time": None})
        self.assertEqual(caught.exception.code, "PROCESS_QUERY_FAILED")

    def test_private_synthetic_install_preserves_definitions_and_map(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            base = Path(temporary)
            stage, config, imported, mapping = create_stage(base)
            root = base / "managed"
            with patch.object(bridge, "protect_directory"), patch.object(bridge, "verify_acl"), patch.object(bridge, "_validate_core") as validation:
                result = bridge.install(root, stage)
                self.assertTrue(result["installed"])
                validation.assert_called_once_with(root)
                self.assertEqual(bridge.read_yaml(root / "config.yaml"), config)
                self.assertEqual(bridge.read_yaml(root / "import.yaml"), imported)
                self.assertEqual(bridge.read_json(root / "port-map.json"), mapping)
                self.assertFalse((root / "data" / "cache.db").exists())
                self.assertTrue(bridge.install(root, stage)["unchanged"])
                imported["rules"].append("DOMAIN,example.invalid,REJECT")
                (stage / bridge.STAGE_IMPORT).write_bytes(bridge.yaml_bytes(imported))
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.install(root, stage)
                self.assertEqual(caught.exception.code, "STAGE_CHANGED_UPDATE_NOT_IMPLEMENTED")

    def test_unmanaged_directory_is_never_overwritten(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            base = Path(temporary)
            stage, *_ = create_stage(base)
            root = base / "unmanaged"
            root.mkdir()
            precious = root / "core.exe"
            precious.write_bytes(b"do-not-overwrite")
            with self.assertRaises(bridge.BridgeError):
                bridge.install(root, stage)
            self.assertEqual(precious.read_bytes(), b"do-not-overwrite")

    def test_native_and_frozen_launchers_use_correct_entrypoint(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary)
            bridge._shortcut(root, "start")
            self.assertIn(" -B ", (root / "start.ps1").read_text(encoding="utf-8-sig"))
            with patch.object(sys, "frozen", True, create=True):
                bridge._shortcut(root, "start")
            text = (root / "start.ps1").read_text(encoding="utf-8-sig")
            self.assertNotIn(" -B ", text)
            self.assertNotIn("feiniao_bridge.py", text)
            self.assertIn(" --root ", text)

    @unittest.skipUnless(os.name == "nt", "Windows ACL")
    def test_native_acl_is_private_only_in_temporary_directory(self):
        with TemporaryDirectory(prefix="fb-unit-") as temporary:
            root = Path(temporary) / "private"
            root.mkdir()
            bridge.protect_directory(root)
            bridge.verify_acl(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
