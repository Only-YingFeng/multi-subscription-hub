"""Qt controller acceptance with a fully mocked local backend.

No subscription, core, system proxy, or browser storage is touched.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.dont_write_bytecode = True
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import controller
import view


def synthetic_sources():
    return [
        {"id": "source-one", "name": "订阅一", "nodeCount": 69, "available": True, "current": True},
        {"id": "source-two", "name": "飞鸟加速", "nodeCount": 108, "available": True, "isFeiniao": True},
    ]


def synthetic_rows(count=177, tested=False):
    return [{"id": f"demo-{index}", "nodeName": f"示例节点 {index + 1}",
             "sourceId": "source-one" if index < 69 else "source-two",
             "port": 46000 + index, **({"ok": True, "ms": 50 + index % 90} if tested else {"status": "untested"})}
            for index in range(count)]


class WorkerTests(unittest.TestCase):
    def worker_result(self, response):
        worker = controller.Worker("start", lambda _progress: response)
        results, errors = [], []
        worker.result.connect(lambda operation, result: results.append((operation, result)))
        worker.failed.connect(lambda operation, error: errors.append((operation, error)))
        worker.run()
        worker.deleteLater()
        return results, errors

    def test_ok_false_is_failure_even_without_exception(self):
        results, errors = self.worker_result({"ok": False, "code": "CORE_START_FAILED"})
        self.assertEqual([], results)
        self.assertEqual([("start", "CORE_START_FAILED")], errors)

    def test_nested_discovery_and_status_failure_are_not_success(self):
        for key in ("discovery", "status"):
            with self.subTest(child=key):
                results, errors = self.worker_result({key: {"ok": False, "error": "LOCAL_OPERATION_FAILED"}})
                self.assertEqual([], results)
                self.assertEqual([("start", "LOCAL_OPERATION_FAILED")], errors)

    def test_unsafe_error_code_never_reaches_ui_signal(self):
        results, errors = self.worker_result({"ok": False, "code": "https://example.invalid/private-token"})
        self.assertEqual([], results)
        self.assertEqual([("start", "RuntimeError")], errors)


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for filename in ("msyh.ttc", "msyhbd.ttc"):
                font = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / filename
                if font.is_file():
                    QFontDatabase.addApplicationFont(str(font))
        view.configure_application(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="multi-hub-controller-test-")
        self.root = Path(self.temporary.name).resolve()
        self.backend = MagicMock(name="FullyMockedBackend")
        self.backend.root = self.root
        self.backend.clash_dir = self.root / "synthetic-clash"
        self.backend.bridge_root = self.root / "synthetic-bridge"
        self.backend.core_path = self.root / "synthetic-core.exe"
        self.backend.discover.return_value = {"ok": True, "sources": synthetic_sources()}
        self.backend.preview.return_value = {"ok": True, "rows": synthetic_rows()}
        self.backend.status.return_value = {"ok": True, "running": False, "sourceIds": [], "allListenersPrivate": False}
        self.backend.prepare.return_value = {"ok": True, "prepared": True, "entries": synthetic_rows()}
        self.backend.start.return_value = {"ok": True, "running": True, "sourceIds": ["source-one", "source-two"], "allListenersPrivate": True}
        self.backend.stop.return_value = {"ok": True, "running": False, "allListenersPrivate": False}
        self.backend.test_nodes.return_value = {"ok": True, "rows": synthetic_rows(tested=True)}
        self.window = view.Window()
        self.window.show()
        # Automatic polling and deferred prepare/start callbacks are disabled;
        # each asynchronous operation under test is requested explicitly.
        self.shot_patch = patch.object(controller.QTimer, "singleShot")
        self.shot_mock = self.shot_patch.start()
        self.tray_patch = patch.object(controller.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)
        self.tray_patch.start()
        self.control = controller.Controller(self.window, self.backend, output_dir=self.root / "output")
        self.control.timer.stop()
        self.app.processEvents()

    def tearDown(self):
        self.wait_idle()
        self.control.timer.stop()
        self.control.tray.hide()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.shot_patch.stop()
        self.tray_patch.stop()
        self.temporary.cleanup()

    def wait_idle(self):
        for _ in range(200):
            self.app.processEvents()
            if self.control.worker is None:
                return
            QTest.qWait(5)
        self.fail("Mock backend worker did not finish")

    def discover(self):
        self.control.refresh()
        self.wait_idle()

    def set_running(self):
        self.discover()
        self.control.rows = self.control._normalize_rows(synthetic_rows())
        self.window.set_nodes(self.control.rows)
        self.control.last_status = self.backend.start.return_value.copy()
        self.control.running_sources = ["source-one", "source-two"]
        self.control._update_status()

    def test_discovery_sets_real_count_names_and_selected_sources(self):
        self.discover()
        self.assertEqual(["source-one", "source-two"], self.window.get_selected_sources())
        self.assertEqual("2", self.window.source_metric.text())
        self.assertIn("飞鸟桥接", self.window._source_cards["source-two"].hint.text())
        self.backend.discover.assert_called_once_with()
        self.backend.start.assert_not_called()

    def test_preview_rows_response_populates_all_177_nodes(self):
        self.discover()
        self.control._run("preview", lambda _p: self.backend.preview(["source-one", "source-two"]), visible=False)
        self.wait_idle()
        self.assertEqual(177, self.window.table.rowCount())
        self.assertEqual("订阅一", self.window.table.item(0, 1).text())
        self.assertEqual("飞鸟加速", self.window.table.item(176, 1).text())
        self.assertEqual("127.0.0.1:46176", self.window.table.item(176, 2).text())
        self.assertEqual("未检测", self.window.table.item(0, 3).text())
        self.backend.prepare.assert_not_called()
        self.backend.start.assert_not_called()

    def test_real_qt_deferred_discovery_preview_chain_finishes_without_reentry(self):
        # Restore real Qt zero-delay dispatch to cover the production chain,
        # while every backend call remains an in-memory mock.
        self.shot_patch.stop()
        try:
            self.control.refresh()
            for _ in range(200):
                self.app.processEvents()
                if self.control.worker is None and self.window.table.rowCount() == 177:
                    break
                QTest.qWait(5)
            else:
                self.fail("Deferred discover/preview mock chain did not settle")
            self.backend.discover.assert_called_once_with()
            self.backend.preview.assert_called_once_with(["source-one", "source-two"])
            self.backend.prepare.assert_not_called()
            self.backend.start.assert_not_called()
        finally:
            self.shot_mock = self.shot_patch.start()

    def test_test_rows_and_ms_response_displays_177_chinese_available_states(self):
        self.set_running()
        self.control.test()
        self.wait_idle()
        self.assertEqual(177, self.window.table.rowCount())
        self.assertEqual("可用 · 50 ms", self.window.table.item(0, 3).text())
        self.assertTrue(self.window.table.item(176, 3).text().startswith("可用 ·"))
        self.assertIn("177/177", self.window.message_label.text())
        self.assertEqual("running", self.window.start_button.property("phase"))

    def test_export_files_response_exposes_full_bak_and_mapping_paths(self):
        self.set_running()
        bak = str(self.root / "output" / "ZeroOmega-多订阅-手动选择-独立分流.bak")
        mapping = str(self.root / "output" / "节点与端口映射表.md")
        self.backend.export_bak.return_value = {"ok": True, "files": {"bak": bak, "mapping": mapping}}
        self.control.export(str(self.root / "output"))
        self.wait_idle()
        self.assertEqual(bak, self.window.bak_edit.toolTip())
        self.assertEqual(mapping, self.window.map_edit.toolTip())
        self.assertEqual(Path(bak).name, self.window.bak_edit.text())
        self.assertTrue(self.window.bak_copy.isEnabled())
        self.assertEqual("complete", self.window.export_button.property("phase"))

    def test_pending_source_changes_disable_export_and_test_until_applied(self):
        self.set_running()
        self.assertTrue(self.window.export_button.isEnabled())
        self.window.set_selected_sources(["source-one"])
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertFalse(self.window.test_button.isEnabled())
        self.assertEqual("应用选择", self.window.prepare_button.text())
        self.assertTrue(self.window.prepare_button.isEnabled())
        self.assertEqual("pending", self.window.prepare_button.property("phase"))
        self.backend.prepare.assert_not_called()
        self.backend.start.assert_not_called()

    def test_failed_start_result_never_reports_running_or_success(self):
        self.discover()
        self.backend.start.return_value = {"ok": False, "code": "CORE_START_FAILED"}
        self.control._start_prepared()
        self.wait_idle()
        self.assertFalse(self.control.last_status.get("running"))
        self.assertNotEqual("running", self.window.start_button.property("phase"))
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertIn("失败", self.window.message_label.text())

    def test_start_without_running_confirmation_does_not_claim_running(self):
        self.discover()
        self.backend.start.return_value = {"ok": True, "running": False, "allListenersPrivate": False}
        self.control._start_prepared()
        self.wait_idle()
        self.assertFalse(self.control.last_status.get("running"))
        self.assertNotEqual("已运行", self.window.start_button.text())
        self.assertFalse(self.window.export_button.isEnabled())

    def test_poll_stopped_clears_running_state_and_green_test_claim(self):
        self.set_running()
        self.control._result("test", {"ok": True, "rows": synthetic_rows(tested=True)})
        self.backend.status.return_value = {"ok": True, "running": False, "allListenersPrivate": False, "sourceIds": []}
        self.control.poll()
        self.wait_idle()
        self.assertNotEqual("已运行", self.window.start_button.text())
        self.assertEqual("后台未运行", self.window.backend_badge.text())
        self.assertIn("上次可用", self.window.table.item(0, 3).text())
        self.assertFalse(self.window.export_button.isEnabled())

    def test_poll_failure_does_not_continue_verified_green_running_claim(self):
        self.set_running()
        self.backend.status.return_value = {"ok": False, "code": "PROCESS_IDENTITY_MISMATCH"}
        self.control.poll()
        self.wait_idle()
        self.assertNotEqual("running", self.window.start_button.property("phase"))
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertIn("身份", self.window.message_label.text())

    def test_settings_calls_exact_view_api_and_rejects_private_root_migration(self):
        other = self.root / "different-private-root"
        with patch.object(self.window, "show_settings", return_value={"privateRoot": str(other)}) as dialog, patch("backend.Backend") as factory, patch("bridge_lifecycle.atomic_json") as save:
            self.control.settings()
            dialog.assert_called_once()
            values = dialog.call_args.args[0]
            self.assertEqual(str(self.root), values["privateRoot"])
            self.assertEqual(str(self.backend.clash_dir), values["clashData"])
            self.assertEqual(str(self.backend.bridge_root), values["bridgeRoot"])
            self.assertEqual(str(self.backend.core_path), values["corePath"])
            factory.assert_not_called()
            save.assert_not_called()
        self.assertIn("迁移", self.window.message_label.text())
        self.assertIs(self.backend, self.control.backend)

    def test_settings_reject_changes_while_backend_running(self):
        self.set_running()
        with patch.object(self.window, "show_settings", return_value={"privateRoot": str(self.root), "clashData": str(self.root / "other")}), patch("backend.Backend") as factory, patch("bridge_lifecycle.atomic_json") as save:
            self.control.settings()
            factory.assert_not_called()
            save.assert_not_called()
        self.assertIn("先停止", self.window.message_label.text())

    def test_settings_saved_paths_refresh_and_policy_return_contract(self):
        replacement = MagicMock(name="MockReplacementBackend")
        replacement.root = self.root
        replacement.clash_dir = self.root / "alternate-clash"
        replacement.bridge_root = self.root / "alternate-bridge"
        replacement.core_path = self.root / "alternate-core.exe"
        replacement._path.return_value = self.root / "ui-settings.json"
        values = {"privateRoot": str(self.root), "clashData": str(replacement.clash_dir),
                  "bridgeRoot": str(replacement.bridge_root), "corePath": str(replacement.core_path),
                  "policies": {"其他流量": "PROXY"}}
        with patch.object(self.window, "show_settings", return_value=values), patch("backend.Backend", return_value=replacement) as factory, patch("bridge_lifecycle.atomic_json") as save, patch.object(self.control, "refresh") as refresh:
            self.control.settings()
            factory.assert_called_once_with(self.root, clash_dir=values["clashData"], core_path=values["corePath"], bridge_root=values["bridgeRoot"])
            replacement._ensure_root.assert_called_once_with()
            save.assert_called_once()
            self.assertEqual(values["clashData"], save.call_args.args[1]["clashData"])
            self.assertEqual(values["bridgeRoot"], save.call_args.args[1]["bridgeRoot"])
            self.assertEqual(values["corePath"], save.call_args.args[1]["corePath"])
            refresh.assert_called_once_with()
        self.assertIs(replacement, self.control.backend)
        self.assertEqual({"其他流量": "PROXY"}, self.control.policy_override)


if __name__ == "__main__":
    unittest.main()
