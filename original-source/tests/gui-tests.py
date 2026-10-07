"""Real Tk layout/state tests with a withdrawn window and a fully mocked API.

Run with: python -B tools/clash-zeroomega-app/tests/gui-tests.py
No real Clash API, browser, registry, application install, or network is used.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import types
import unittest
from unittest.mock import ANY, Mock, patch


sys.dont_write_bytecode = True
APP_DIR = Path(__file__).resolve().parent.parent


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = types.ModuleType("portable")
        cls.backend.VERSION = "1.0.1"
        cls.backend.PublicError = type("PublicError", (RuntimeError,), {})
        cls.backend.m = types.SimpleNamespace(RESOURCES=APP_DIR)
        for name in ("detect", "check_nodes", "generate", "apply", "rollback", "safe_error", "review_policies"):
            setattr(cls.backend, name, Mock())
        spec = importlib.util.spec_from_file_location("tested_clash_gui", APP_DIR / "gui.py")
        cls.gui = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"portable": cls.backend}):
            spec.loader.exec_module(cls.gui)

    def setUp(self):
        for name in ("detect", "check_nodes", "generate", "apply", "rollback", "safe_error", "review_policies"):
            getattr(self.backend, name).reset_mock(return_value=True, side_effect=True)
        self.backend.safe_error.side_effect = lambda error: "操作未完成（" + type(error).__name__ + "）。"
        self.backend.check_nodes.return_value = self.checks()
        self.temporary = tempfile.TemporaryDirectory(prefix="clash-gui-test-")
        self.directory = Path(self.temporary.name)
        self.dialog_patches = [patch.object(self.gui.messagebox, name, return_value=False) for name in ("showerror", "showinfo", "askyesno")]
        self.dialogs = [item.start() for item in self.dialog_patches]
        self.root = tk.Tk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda kind, value, trace: self.callback_errors.append(kind.__name__)
        self.app = self.gui.App(self.root)
        self.root.geometry("900x650+20000+20000")
        # Tk needs one map to allocate real child geometry. It stays outside
        # the desktop, is immediately withdrawn, and is never a resident UI.
        self.root.deiconify()
        self.root.update()
        self.root.withdraw()
        self.root.update()

    def tearDown(self):
        try:
            self.assertFalse(self.callback_errors, "Tk callback raised an exception")
        finally:
            if self.root.winfo_exists():
                self.app._busy = False
                self.app._close()
            for item in self.dialog_patches:
                item.stop()
            self.temporary.cleanup()

    def context(self, installed=False):
        return {
            "ok": True, "issues": [], "clashVersion": "2.5.7",
            "coreVersion": "1.19.32", "mode": "rule", "nodeCount": 2,
            "groupCount": 1, "outputSuggested": str(self.directory / "exports"),
            "canGenerate": True, "canApply": True,
            "hasInstallation": installed, "needsPolicyReview": False,
            "groupPolicies": [], "_private": object(), "_nodeChecks": self.checks(),
        }

    def checks(self):
        return {"checks": [
            {"nodeName": "测试节点", "status": "reachable", "delayMs": 17},
            {"nodeName": "第二节点", "status": "timeout", "delayMs": None},
        ], "total": 2, "checkedAt": "2026-10-06T00:00:00Z"}

    def generation(self, needs_apply=True):
        return {
            "bakPath": str(self.directory / "exports" / "nodes.bak"),
            "mapPath": str(self.directory / "exports" / "ports.md"),
            "extensionPath": str(self.directory / "clash-data" / "profiles" / "script.js"),
            "new": 1, "active": 1, "inactive": 1, "needsApply": needs_apply,
            "entries": [
                {"nodeName": "测试节点", "profileName": "测试情景", "port": 15001, "active": True, "status": "new"},
                {"profileName": "失效情景", "port": 15002, "active": False, "status": "inactive"},
            ],
        }

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("The mocked GUI task did not finish in time")

    def detect_successfully(self, installed=False):
        self.backend.detect.return_value = self.context(installed)
        self.app.detect_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.assertEqual(self.app.headline.get(), "探测成功")

    def generate_successfully(self, needs_apply=True):
        self.backend.generate.return_value = self.generation(needs_apply)
        self.app.generate_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.assertEqual(self.app.headline.get(), "备份已生成")

    def button_state(self, name):
        return str(getattr(self.app, name + "_button").cget("state"))

    def test_initial_permissions(self):
        self.assertEqual(self.root.winfo_width(), 900)
        self.assertEqual(self.root.winfo_height(), 650)
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertEqual(self.button_state("detect"), "normal")
        self.assertEqual(self.app.writeback_status.get(), "生成后校验")
        self.assertFalse(self.app.table.get_children())
        for name in ("generate", "apply", "rollback", "open"):
            self.assertEqual(self.button_state(name), "disabled")
        self.app.generate_button.invoke()
        self.app.generate()
        self.backend.generate.assert_not_called()

    def test_detect_then_generate_permissions_and_rows(self):
        self.detect_successfully()
        self.assertEqual(self.button_state("generate"), "normal")
        self.assertEqual(self.button_state("apply"), "disabled")
        self.assertEqual(self.app.stats["mode"].get(), "规则模式")
        self.backend.detect.assert_called_once_with(data_directory=None, clash_directory=None, state_directory=None)
        self.backend.check_nodes.assert_called_once_with(self.app._context, on_progress=ANY)
        self.assertEqual(self.app.table.item("0", "values"), ("测试节点", "待生成", "可用 · 17 ms"))
        self.assertEqual(self.app.table.item("1", "values"), ("第二节点", "待生成", "超时"))
        self.generate_successfully()
        self.assertEqual(self.button_state("apply"), "normal")
        self.assertEqual(self.button_state("rollback"), "disabled")
        self.assertEqual(self.app.table.item("0", "values"), ("测试节点", "127.0.0.1:15001", "可用 · 17 ms"))
        self.assertEqual(self.app.table.item("1", "values"), ("失效情景", "127.0.0.1:15002", "已失效"))
        self.assertEqual(self.app.writeback_status.get(), "待写回")
        self.assertEqual(self.app.extension_path.get(), self.generation()["extensionPath"])
        self.backend.apply.assert_not_called()

    def test_already_applied_generation_disables_apply(self):
        self.detect_successfully(installed=True)
        self.generate_successfully(needs_apply=False)
        self.assertEqual(self.button_state("apply"), "disabled")
        self.assertEqual(self.button_state("rollback"), "normal")
        self.assertIn("无需重复写回", self.app.status_hint.get())
        self.assertEqual(self.app.writeback_status.get(), "已写回并生效")

    def test_failed_detection_clears_old_permissions(self):
        self.detect_successfully(installed=True)
        self.generate_successfully()
        self.backend.detect.return_value = {"ok": False, "issues": ["请选择程序目录。"]}
        self.backend.check_nodes.reset_mock()
        self.app.detect_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.assertIsNone(self.app._context)
        self.assertIsNone(self.app._generation)
        self.assertFalse(self.app.table.get_children())
        for name in ("generate", "apply", "rollback"):
            self.assertEqual(self.button_state(name), "disabled")
        self.assertIn("请选择程序目录", self.app.log.get("1.0", "end"))
        self.backend.check_nodes.assert_not_called()
        self.assertEqual(self.app.writeback_status.get(), "生成后校验")

    def test_every_path_change_clears_old_context(self):
        for name in ("output", "data", "clash", "state", "template"):
            with self.subTest(path=name):
                self.app._finish_detect(self.context(installed=True))
                self.app._finish_generate(self.generation())
                self.app._refresh_buttons()
                self.app.paths[name].set(str(self.directory / (name + "-changed")))
                self.assertIsNone(self.app._context)
                self.assertIsNone(self.app._generation)
                self.assertFalse(self.app.table.get_children())
                self.assertEqual(self.app.extension_path.get(), "")
                self.assertEqual(self.app.writeback_status.get(), "生成后校验")
                for button in ("generate", "apply", "rollback"):
                    self.assertEqual(self.button_state(button), "disabled")

    def test_busy_lock_prevents_repeated_actions(self):
        release = threading.Event()
        def delayed_detect(**_kwargs):
            release.wait(2)
            return self.context()
        self.backend.detect.side_effect = delayed_detect
        try:
            self.app.detect()
            self.pump_until(lambda: self.backend.detect.call_count == 1)
            for name in ("detect", "generate", "apply", "rollback", "open"):
                self.assertEqual(self.button_state(name), "disabled")
            for widget in self.app._path_widgets:
                self.assertEqual(str(widget.cget("state")), "disabled")
            self.app.detect()
            self.app.generate()
            self.app.apply()
            self.app.rollback()
            self.assertEqual(self.backend.detect.call_count, 1)
            self.backend.generate.assert_not_called()
            self.backend.apply.assert_not_called()
            self.backend.rollback.assert_not_called()
        finally:
            release.set()
            self.pump_until(lambda: not self.app._busy)
        self.assertEqual(self.button_state("generate"), "normal")
        for widget in self.app._path_widgets:
            self.assertEqual(str(widget.cget("state")), "normal")

    def test_cancel_apply_and_rollback_confirmation(self):
        self.detect_successfully(installed=True)
        self.generate_successfully()
        self.app.apply_button.invoke()
        self.app.rollback_button.invoke()
        self.assertEqual(self.dialogs[2].call_count, 2)
        title, message = self.dialogs[2].call_args_list[0].args[:2]
        self.assertEqual(title, "确认写回 Clash 扩展配置")
        self.assertIn("不会向机场拉取订阅", message)
        self.assertIn("127.0.0.1", message)
        self.assertIn("扩展脚本写回", message)
        self.assertIn("私有备份", message)
        self.assertIn("短暂中断连接", message)
        self.assertEqual(self.dialogs[2].call_args_list[0].kwargs["default"], "no")
        self.backend.apply.assert_not_called()
        self.backend.rollback.assert_not_called()
        self.assertFalse(self.app._busy)
        self.assertEqual(self.button_state("apply"), "normal")
        self.assertEqual(self.button_state("rollback"), "normal")

    def test_progress_keeps_generation_locked_until_node_checks_finish(self):
        release = threading.Event()
        callback_threads = []
        self.backend.detect.return_value = self.context()
        def check_nodes(_context, on_progress):
            callback_threads.append(threading.get_ident())
            on_progress(self.checks()["checks"][0])
            release.wait(2)
            return self.checks()
        self.backend.check_nodes.side_effect = check_nodes
        try:
            self.app.detect()
            self.pump_until(lambda: "1 / 2" in self.app.headline.get())
            self.assertTrue(self.app._busy)
            self.assertNotEqual(callback_threads[0], threading.get_ident())
            self.assertEqual(self.button_state("generate"), "disabled")
            self.assertEqual(self.button_state("apply"), "disabled")
            self.backend.generate.assert_not_called()
        finally:
            release.set()
            self.pump_until(lambda: not self.app._busy)
        self.assertEqual(self.app.headline.get(), "探测成功")
        self.assertEqual(self.button_state("generate"), "normal")

    def test_generation_keeps_failed_and_untested_nodes_with_real_results(self):
        context = self.context()
        context["_nodeChecks"]["checks"].append({"nodeName": "失败节点", "status": "failed", "delayMs": None})
        self.app._finish_detect(context)
        generation = self.generation()
        generation["entries"] = [
            {"nodeName": name, "port": 15001 + index, "active": True, "status": "new"}
            for index, name in enumerate(("测试节点", "第二节点", "失败节点", "未检测节点"))
        ]
        generation["active"], generation["inactive"] = 4, 0
        self.app._finish_generate(generation)
        rows = [self.app.table.item(item, "values") for item in self.app.table.get_children()]
        self.assertEqual([row[2] for row in rows], ["可用 · 17 ms", "超时", "测试失败", "未测试"])
        self.assertEqual(len(rows), 4)
        self.assertNotIn("在订阅中", [row[2] for row in rows])
        self.assertEqual(self.app.writeback_status.get(), "待写回")

    def test_missing_or_invalid_delay_never_claims_a_node_is_available(self):
        for check in ({}, {"status": "reachable"}, {"status": "reachable", "delayMs": True}, {"status": "reachable", "delayMs": -1}, {"status": "PRIVATE_UNKNOWN_STATUS"}):
            with self.subTest(check=check):
                self.assertEqual(self.app._node_status(check), ("未测试", "untested"))

    def test_confirmed_apply_shows_success_only_after_live_result(self):
        self.detect_successfully()
        self.generate_successfully()
        self.dialogs[2].return_value = True
        self.backend.apply.return_value = {"status": "installed-and-live", "extensionPath": self.generation()["extensionPath"], "summaryWarning": "安装账本记录需要在下次运行时复核。"}
        self.app.apply_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.backend.apply.assert_called_once_with(self.app._context)
        self.assertEqual(self.app.headline.get(), "写回完成")
        self.assertEqual(self.app.writeback_status.get(), "已写回并生效")
        self.assertEqual(self.button_state("apply"), "disabled")
        self.assertEqual(self.button_state("rollback"), "normal")
        self.assertIn("安装账本记录需要", self.app.log.get("1.0", "end"))

    def test_invalid_apply_result_cannot_claim_configuration_is_live(self):
        self.detect_successfully()
        self.generate_successfully()
        self.dialogs[2].return_value = True
        self.backend.apply.return_value = {"status": "prepared", "privatePayload": "PRIVATE_DO_NOT_DISPLAY"}
        self.app.apply_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.assertEqual(self.app.headline.get(), "操作未完成")
        self.assertEqual(self.app.writeback_status.get(), "生成后校验")
        self.assertIsNone(self.app._context)
        self.assertEqual(self.button_state("apply"), "disabled")
        self.assertNotIn("PRIVATE", self.app.log.get("1.0", "end"))

    def test_connectivity_failure_uses_safe_error_without_enabling_generation(self):
        self.backend.detect.return_value = self.context()
        self.backend.check_nodes.side_effect = RuntimeError("PRIVATE_NETWORK_VALUE")
        self.app.detect()
        self.pump_until(lambda: not self.app._busy)
        self.assertIsNone(self.app._context)
        self.assertEqual(self.button_state("generate"), "disabled")
        self.assertNotIn("PRIVATE", self.app.log.get("1.0", "end"))

    def test_all_worker_errors_pass_through_safe_error(self):
        for error in (RuntimeError("PRIVATE_RUNTIME_VALUE=DO_NOT_DISPLAY"), ValueError("PRIVATE_PARAMETER_VALUE=DO_NOT_DISPLAY")):
            with self.subTest(kind=type(error).__name__):
                self.backend.detect.side_effect = error
                self.app.detect()
                self.pump_until(lambda: not self.app._busy)
                self.backend.safe_error.assert_called_with(error)
                displayed = self.app.log.get("1.0", "end") + self.app.status_hint.get()
                self.assertNotIn("PRIVATE", displayed)
                self.assertIn(type(error).__name__, displayed)
                self.assertIsNone(self.app._context)

    def test_safe_error_filter_failure_is_still_redacted(self):
        self.backend.safe_error.side_effect = ValueError("PRIVATE_FILTER_ERROR")
        self.backend.detect.side_effect = RuntimeError("PRIVATE_BACKEND_ERROR")
        self.app.detect()
        self.pump_until(lambda: not self.app._busy)
        displayed = self.app.log.get("1.0", "end")
        self.assertNotIn("PRIVATE", displayed)
        self.assertIn("RuntimeError", displayed)

    def review_context(self, count=2):
        context = self.context()
        context["needsPolicyReview"] = True
        context["groupPolicies"] = [
            {"name": "混合组 " + str(index), "choices": ["DIRECT", "PROXY"], "currentAction": "DIRECT"}
            for index in range(count)
        ]
        return context

    def open_policy_review(self, count=2):
        self.app._finish_detect(self.review_context(count))
        self.app._refresh_buttons()
        self.app.generate_button.invoke()
        self.root.update()
        self.assertIsNotNone(self.app._policy_dialog)
        self.app._policy_dialog.window.withdraw()
        return self.app._policy_dialog

    def test_policy_review_starts_without_default_choices(self):
        dialog = self.open_policy_review()
        for name, variable in dialog.variables.items():
            self.assertEqual(variable.get(), "请选择")
            self.assertEqual(str(dialog.comboboxes[name].cget("state")), "readonly")
            self.assertEqual(tuple(dialog.comboboxes[name].cget("values")), ("直连", "浏览器所选节点"))
        self.assertEqual(str(dialog.confirm_button.cget("state")), "disabled")
        dialog.confirm()
        self.backend.review_policies.assert_not_called()
        self.backend.generate.assert_not_called()
        dialog.cancel()

    def test_policy_review_cancel_does_not_generate(self):
        dialog = self.open_policy_review()
        dialog.cancel_button.invoke()
        self.assertIsNone(self.app._policy_dialog)
        self.assertIsNone(self.app._pending_generation)
        self.backend.review_policies.assert_not_called()
        self.backend.generate.assert_not_called()
        self.assertTrue(self.app._context["needsPolicyReview"])

    def test_policy_review_only_accepts_allowed_actions(self):
        dialog = self.open_policy_review()
        for variable in dialog.variables.values():
            variable.set("未允许的动作")
        dialog.confirm()
        self.backend.review_policies.assert_not_called()
        self.backend.generate.assert_not_called()
        self.assertEqual(str(dialog.confirm_button.cget("state")), "disabled")
        dialog.cancel()

    def test_policy_review_confirmation_generates_after_backend_review(self):
        dialog = self.open_policy_review()
        order = []
        self.backend.review_policies.side_effect = lambda _context, _choices: order.append("review")
        def generate(_context, **_kwargs):
            order.append("generate")
            return self.generation()
        self.backend.generate.side_effect = generate
        for variable in dialog.variables.values():
            variable.set("浏览器所选节点")
        self.assertEqual(str(dialog.confirm_button.cget("state")), "normal")
        dialog.confirm_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.backend.review_policies.assert_called_once_with(self.app._context, {"混合组 0": "PROXY", "混合组 1": "PROXY"})
        self.backend.generate.assert_called_once()
        self.assertFalse(self.app._context["needsPolicyReview"])
        self.assertEqual(self.app.headline.get(), "备份已生成")
        self.assertIsNone(self.app._policy_dialog)
        self.assertEqual(order, ["review", "generate"])

    def test_policy_review_error_prevents_generation(self):
        dialog = self.open_policy_review()
        self.backend.review_policies.side_effect = RuntimeError("PRIVATE_REVIEW_FAILURE")
        for variable in dialog.variables.values():
            variable.set("直连")
        dialog.confirm_button.invoke()
        self.pump_until(lambda: not self.app._busy)
        self.backend.generate.assert_not_called()
        self.assertIsNone(self.app._context)
        self.assertNotIn("PRIVATE", self.app.log.get("1.0", "end"))

    def test_many_policy_groups_are_scrollable(self):
        dialog = self.open_policy_review(count=24)
        # As with the main window, measure a short offscreen map, then hide.
        self.root.deiconify()
        dialog.window.deiconify()
        dialog.window.update()
        self.root.update()
        self.assertGreater(dialog.canvas.bbox("all")[3], dialog.canvas.winfo_height())
        self.assertLess(dialog.canvas.yview()[1], 1)
        dialog.canvas.yview_moveto(1)
        self.root.update()
        self.assertAlmostEqual(dialog.canvas.yview()[1], 1, places=3)
        dialog.cancel()
        self.root.withdraw()

    def test_cached_policy_does_not_open_review(self):
        self.detect_successfully()
        self.generate_successfully()
        self.assertIsNone(self.app._policy_dialog)
        self.backend.review_policies.assert_not_called()

    def test_advanced_paths_scroll_without_overlap_at_minimum_size(self):
        # Windows retains cached native child positions while withdrawn.
        # Measure scrolling offscreen, then return to the withdrawn state.
        self.root.deiconify()
        self.app._toggle_advanced()
        self.root.update()
        canvas = self.app.sidebar_canvas
        bounds = canvas.bbox("all")
        self.assertGreater(bounds[3], canvas.winfo_height())
        self.assertLess(canvas.yview()[1], 1)
        controls = self.app._path_widgets + [self.app.extension_entry]
        fixed_controls = [self.app.detect_button, self.app.generate_button, self.app.apply_button, self.app.open_button, self.app.rollback_button]
        fixed_positions = {widget: (widget.winfo_rootx(), widget.winfo_rooty()) for widget in fixed_controls}
        for widget in controls:
            # Bring each control into view without requiring the window to be shown.
            canvas.yview_moveto(0)
            self.root.update()
            position = widget.winfo_rooty() - canvas.winfo_rooty()
            canvas.yview_moveto(max(0, position - canvas.winfo_height() / 2) / bounds[3])
            self.root.update()
            top, bottom = widget.winfo_rooty(), widget.winfo_rooty() + widget.winfo_height()
            viewport_top = canvas.winfo_rooty()
            self.assertGreaterEqual(top, viewport_top - 1)
            self.assertLessEqual(bottom, viewport_top + canvas.winfo_height() + 1)
        canvas.yview_moveto(1)
        self.root.update()
        self.assertAlmostEqual(canvas.yview()[1], 1, places=3)
        body = self.app.table.master.master
        outer = body.master
        boxes = sorted((int(widget.grid_info()["row"]), widget.winfo_y(), widget.winfo_height()) for widget in outer.grid_slaves())
        for first, second in zip(boxes, boxes[1:]):
            self.assertLessEqual(first[1] + first[2], second[1])
        self.assertGreater(self.app.table.winfo_height(), 160)
        for _row, top, height in boxes:
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(top + height, outer.winfo_height())
        table_card = self.app.table.master
        sidebar_host = canvas.master
        self.assertLessEqual(table_card.winfo_x() + table_card.winfo_width(), sidebar_host.winfo_x())
        self.assertGreaterEqual(table_card.winfo_width(), 450)
        self.assertGreaterEqual(sidebar_host.winfo_width(), 330)
        step_buttons = [self.app.detect_button, self.app.generate_button, self.app.apply_button]
        for first, second in zip(step_buttons, step_buttons[1:]):
            self.assertEqual(first.winfo_y(), second.winfo_y())
            self.assertLessEqual(first.winfo_x() + first.winfo_width(), second.winfo_x())
        for button in step_buttons:
            self.assertGreaterEqual(button.winfo_height(), 40)
            self.assertGreaterEqual(button.winfo_rooty(), self.root.winfo_rooty())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        for button in fixed_controls:
            self.assertEqual((button.winfo_rootx(), button.winfo_rooty()), fixed_positions[button])
            self.assertGreaterEqual(button.winfo_rooty(), self.root.winfo_rooty())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        for button in (self.app.open_button, self.app.rollback_button):
            self.assertGreaterEqual(button.winfo_rooty(), canvas.winfo_rooty() + canvas.winfo_height())
        self.assertEqual(str(self.app.table.heading("status")["text"]), "连通性")
        self.assertGreaterEqual(int(self.app.table.column("status")["width"]), 128)
        self.root.withdraw()
        self.assertEqual(self.root.state(), "withdrawn")

    def test_real_icon_resources_are_kept_for_window_and_header(self):
        self.assertEqual([image.width() for image in self.app._icon_images], [64, 32])
        self.assertIn("1.0.1", self.root.title())

    def test_utility_buttons_are_visible_without_settings_scroll_at_default_size(self):
        self.root.geometry("1120x800+20000+20000")
        self.root.deiconify()
        self.root.update()
        for button in (self.app.open_button, self.app.rollback_button):
            self.assertGreaterEqual(button.winfo_rootx(), self.root.winfo_rootx())
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
            self.assertGreaterEqual(button.winfo_rooty(), self.root.winfo_rooty())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.assertFalse(self.app._advanced_visible)
        self.root.withdraw()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GuiTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({"suite": "gui-withdrawn-tk", "passed": result.testsRun - len(result.failures) - len(result.errors), "total": result.testsRun, "realApiCalls": 0, "browserAccess": False, "visibleDesktopWindow": False, "offscreenLayoutMeasured": True}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
