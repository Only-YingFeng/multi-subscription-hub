"""Pure Qt widget/layout tests. All node data is synthetic; no Clash API is called."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.dont_write_bytecode = True
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QScrollArea
import qt_view

BAK_NAME = "ZeroOmega-全节点-手动选择-Mihomo分流.bak"


def checks():
    return {"checks": [
        {"nodeName": "美国中转815", "status": "reachable", "delayMs": 328},
        {"nodeName": "日本中转811", "status": "reachable", "delayMs": 65535},
        {"nodeName": "香港中转300", "status": "timeout", "delayMs": None},
        {"nodeName": "新加坡中转302", "status": "failed", "delayMs": None},
    ], "total": 4, "checkedAt": "2026-10-06T00:00:00Z"}


def context(directory):
    return {"ok": True, "clashVersion": "2.5.7", "coreVersion": "1.19.32", "mode": "rule", "nodeCount": 4, "outputSuggested": str(directory), "_nodeChecks": checks()}


def generation(directory, needs_apply=True):
    names = [row["nodeName"] for row in checks()["checks"]] + ["未测试节点", "失效线路"]
    return {"bakPath": str(directory / BAK_NAME), "mapPath": str(directory / "节点与端口映射表.md"), "extensionPath": str(directory / "profiles" / "extension.js"), "new": 1, "active": 5, "inactive": 1, "needsApply": needs_apply,
            "entries": [{"nodeName": name, "profileName": name, "port": 20000 + index, "active": index != 5} for index, name in enumerate(names)]}


class QtViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        # The offscreen Qt plugin does not enumerate Windows system fonts.
        # Load the same font family as the real Windows interface for layout.
        if os.name == "nt":
            for filename in ("msyh.ttc", "msyhbd.ttc"):
                path = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / filename
                if path.is_file():
                    QFontDatabase.addApplicationFont(str(path))
        cls.application.setFont(QFont("Microsoft YaHei UI", 10))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="czo-qt-view-")
        self.directory = Path(self.temporary.name)
        self.window = qt_view.Window()
        self.window.resize(1380, 960)
        self.window.show()
        self.application.processEvents()

    def tearDown(self):
        self.window.set_busy(False)
        self.window.advanced_dialog.hide()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def bounds(self, widget):
        point = widget.mapTo(self.window, QPoint(0, 0))
        return point.x(), point.y(), point.x() + widget.width(), point.y() + widget.height()

    def assert_visible_inside(self, widget, parent=None):
        self.assertTrue(widget.isVisible(), widget.objectName())
        left, top, right, bottom = self.bounds(widget)
        if parent is None:
            limits = (0, 0, self.window.width(), self.window.height())
        else:
            limits = self.bounds(parent)
        self.assertGreaterEqual(left, limits[0])
        self.assertGreaterEqual(top, limits[1])
        self.assertLessEqual(right, limits[2])
        self.assertLessEqual(bottom, limits[3])

    def test_initial_window_uses_real_icon_and_material_icons(self):
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.logo_source.isNull())
        self.assertIn("1.0.2", self.window.windowTitle())
        for button in (self.window.apply_button, self.window.open_button, self.window.rollback_button, self.window.minimize_button, self.window.maximize_button, self.window.close_button):
            self.assertTrue(button.property("iconName").startswith("mdi6."))
            self.assertFalse(button.icon().isNull())
        self.assertTrue(self.window.detect_step.isEnabled())
        for widget in (self.window.generate_step, self.window.apply_button, self.window.rollback_button, self.window.open_button):
            self.assertFalse(widget.isEnabled())
        self.assertTrue(self.window.apply_step.isEnabled())

    def test_default_and_minimum_size_show_all_home_controls_in_one_screen(self):
        for width, height in ((1380, 960), (1200, 850), (1100, 760)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.application.processEvents()
                for widget in (self.window.header, self.window.steps_frame, self.window.table_card, self.window.right_panel, self.window.footer):
                    self.assert_visible_inside(widget)
                for widget in (self.window.output_title, self.window.writeback_badge, self.window.paths["output"], self.window.output_browse_button, self.window.advanced_button, self.window.bak_field, self.window.map_field, self.window.info_box, self.window.apply_button, self.window.open_button, self.window.rollback_button):
                    self.assert_visible_inside(widget, self.window.right_panel)
                self.assertFalse(self.window.right_panel.findChildren(QScrollArea))
                self.assertGreater(self.window.table.height(), 180)
                self.assertLessEqual(self.bounds(self.window.table_card)[2], self.bounds(self.window.right_panel)[0])
                self.assertLessEqual(self.bounds(self.window.header)[3], self.bounds(self.window.steps_frame)[1])
                self.assertLessEqual(self.bounds(self.window.steps_frame)[3], self.bounds(self.window.body)[1])
                self.assertLessEqual(self.bounds(self.window.body)[3], self.bounds(self.window.footer)[1])

    def test_paths_changed_only_for_user_or_external_edit(self):
        events = []
        self.window.pathsChanged.connect(lambda: events.append(True))
        self.window.set_path_values({name: str(self.directory / name) for name in self.window.paths})
        self.assertEqual(events, [])
        self.window.paths["output"].setText("new-output")
        self.assertEqual(events, [True])
        self.assertEqual(self.window.path_values()["output"], "new-output")
        self.assertEqual(set(self.window.path_values()), {"output", "data", "clash", "state", "template"})

    def test_detect_suggested_output_is_internal_and_preserves_explicit_output(self):
        events = []
        self.window.pathsChanged.connect(lambda: events.append(True))
        self.window.render_detect(context(self.directory))
        self.assertEqual(events, [])
        self.assertEqual(self.window.path_values()["output"], str(self.directory))
        self.window.set_path_values({"output": "chosen-output"})
        self.window.render_detect(context(self.directory))
        self.assertEqual(self.window.path_values()["output"], "chosen-output")

    def test_advanced_paths_and_extension_are_not_in_home_layout(self):
        for name in ("data", "clash", "state", "template"):
            self.assertTrue(self.window.advanced_dialog.isAncestorOf(self.window.paths[name]))
            self.assertFalse(self.window.paths[name].isVisible())
        self.assertTrue(self.window.advanced_dialog.isAncestorOf(self.window.extension_field))
        self.assertFalse(self.window.extension_field.isVisible())
        self.assertFalse(self.window.advanced_dialog.isVisible())

    def test_clear_results_preserves_user_paths_and_clears_old_state(self):
        self.window.set_path_values({name: str(self.directory / name) for name in self.window.paths})
        previous = self.window.path_values()
        self.window.render_detect(context(self.directory))
        self.window.render_generate(generation(self.directory), checks())
        self.window.clear_results()
        self.assertEqual(self.window.path_values(), previous)
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertEqual(self.window.bak_field.text(), "")
        self.assertEqual(self.window.map_field.text(), "")
        self.assertEqual(self.window.extension_field.text(), "")
        for field in (self.window.bak_field, self.window.map_field, self.window.extension_field):
            self.assertEqual(field.toolTip(), "")
        self.assertEqual(self.window.writeback_badge.text(), "生成后校验")
        self.assertEqual([step.circle.text() for step in (self.window.detect_step, self.window.generate_step, self.window.apply_step)], ["1", "2", "3"])

    def test_detect_and_generate_keep_failed_and_untested_nodes(self):
        self.window.render_detect(context(self.directory))
        self.assertEqual(self.window.table.rowCount(), 4)
        self.assertEqual(self.window.table.item(0, 1).text(), "待生成")
        self.window.render_generate(generation(self.directory), checks())
        self.assertEqual(self.window.table.rowCount(), 6)
        self.assertEqual([self.window.table.item(index, 2).data(Qt.ItemDataRole.AccessibleTextRole) for index in range(6)], ["可用 · 328 ms", "可用 · 65535 ms", "超时", "测试失败", "未测试", "已失效"])
        self.assertEqual([self.window.table.item(index, 2).text() for index in range(6)], [""] * 6)
        self.assertEqual(self.window.table.item(0, 1).text(), "127.0.0.1:20000")
        self.assertEqual(self.window.writeback_badge.text(), "待写回")
        self.assertEqual(self.window.extension_field.text(), generation(self.directory)["extensionPath"])
        self.assertEqual(self.window.table.item(0, 0).toolTip(), "美国中转815")

    def test_only_connectivity_cells_use_status_colors(self):
        self.window.render_generate(generation(self.directory), checks())
        for row in range(6):
            for column in (0, 1):
                self.assertEqual(self.window.table.item(row, column).foreground().color().name(), qt_view.WHITE)
            dots = self.window.table.cellWidget(row, 2).findChildren(qt_view.QLabel)
            self.assertEqual(dots[0].property("iconName"), "mdi6.circle")
        self.assertEqual(self.window.table.item(0, 2).foreground().color().name(), qt_view.GREEN)
        self.assertEqual(self.window.table.item(2, 2).foreground().color().name(), qt_view.RED)
        self.assertEqual(self.window.table.item(3, 2).foreground().color().name(), qt_view.YELLOW)

    def test_failed_detect_cannot_mark_step_completed(self):
        self.window.render_detect(context(self.directory))
        self.assertEqual(self.window.detect_step.circle.property("iconName"), "mdi6.check")
        self.window.render_detect({"ok": False, "issues": ["PRIVATE_VALUE"]})
        self.assertEqual(self.window.detect_step.circle.text(), "1")
        self.assertEqual(self.window.detect_step.circle.property("iconName"), "")
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertIn("探测未通过", self.window.status_label.text())
        self.assertNotIn("PRIVATE", self.window.status_label.text())
        self.assertEqual(self.window.detect_step.phase, "failed")
        self.assertFalse(self.window.detect_button.property("complete"))

    def test_successful_steps_keep_title_completion_color_and_can_rerun(self):
        self.window.render_detect(context(self.directory))
        self.window.render_generate(generation(self.directory), checks())
        self.window.render_apply({"status": "installed-and-live"})
        self.window.set_permissions(True, False, True, True)
        self.application.processEvents()
        for step in (self.window.detect_step, self.window.generate_step, self.window.apply_step):
            self.assertEqual(step.phase, "complete")
            self.assertTrue(step.title_label.property("complete"))
            self.assertEqual(step.circle.property("iconName"), "mdi6.check")
        for button in (self.window.detect_button, self.window.generate_button):
            self.assertTrue(button.isEnabled())
            self.assertIn(button.grab().toImage().pixelColor(6, button.height() // 2).name(), ("#234b47", "#2d6052"))
            self.assertEqual(button.palette().color(qt_view.QPalette.ColorRole.ButtonText).name(), "#a0edbd")
        self.assertEqual(self.window.apply_step.title_label.palette().color(qt_view.QPalette.ColorRole.WindowText).name(), "#78e4ac")
        events = []
        self.window.detectRequested.connect(lambda: events.append("detect"))
        self.window.generateRequested.connect(lambda: events.append("generate"))
        self.window.applyRequested.connect(lambda: events.append("apply"))
        QTest.mouseClick(self.window.detect_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.generate_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.apply_step, Qt.MouseButton.LeftButton)
        self.assertEqual(events, ["detect", "generate"])

    def test_running_and_failed_states_do_not_keep_old_success_title(self):
        self.window.render_detect(context(self.directory))
        self.window.render_generate(generation(self.directory, needs_apply=False), checks())
        self.window.set_permissions(True, False, True, True)
        self.window.tell("正在生成备份…", "正在校验")
        self.window.set_busy(True)
        self.application.processEvents()
        self.assertEqual(self.window.detect_step.phase, "complete")
        self.assertTrue(self.window.detect_button.property("complete"))
        self.assertEqual(self.window.detect_button.grab().toImage().pixelColor(6, self.window.detect_button.height() // 2).name(), "#234b47")
        self.assertEqual(self.window.generate_step.phase, "working")
        self.assertFalse(self.window.generate_button.property("complete"))
        self.assertEqual(self.window.generate_button.grab().toImage().pixelColor(6, self.window.generate_button.height() // 2).name(), "#4b3f2d")
        self.assertFalse(self.window.apply_step.title_label.property("complete"))
        self.window.set_busy(False)
        self.window.clear_results()
        self.window.tell("操作未完成", "请重新探测")
        self.window.set_permissions(False, False, False, True)
        self.application.processEvents()
        self.assertEqual(self.window.generate_step.phase, "failed")
        self.assertFalse(self.window.generate_button.property("complete"))
        self.assertEqual(self.window.generate_button.grab().toImage().pixelColor(6, self.window.generate_button.height() // 2).name(), "#4a2e3c")
        self.assertFalse(self.window.detect_button.property("complete"))
        self.assertFalse(self.window.apply_step.title_label.property("complete"))

    def test_invalid_generation_cannot_create_completed_title(self):
        with self.assertRaises(ValueError):
            self.window.render_generate({"entries": [], "needsApply": False}, checks())
        self.assertFalse(self.window.generate_button.property("complete"))
        self.assertNotEqual(self.window.generate_step.phase, "complete")

    def test_clear_and_rollback_remove_all_completed_titles(self):
        for reset in (self.window.clear_results, self.window.render_rollback):
            with self.subTest(reset=reset.__name__):
                self.window.render_detect(context(self.directory))
                self.window.render_generate(generation(self.directory, needs_apply=False), checks())
                reset()
                self.window.tell("需要重新探测", "路径已变化")
                for step in (self.window.detect_step, self.window.generate_step, self.window.apply_step):
                    self.assertFalse(step.title_label.property("complete"))
                    self.assertNotEqual(step.phase, "complete")
                self.assertFalse(self.window.apply_button.property("complete"))

    def test_manual_resize_grip_is_available_inside_footer(self):
        self.assertTrue(isinstance(self.window.size_grip, qt_view.QSizeGrip))
        self.assert_visible_inside(self.window.size_grip, self.window.footer)

    def test_missing_or_invalid_delay_is_never_reachable(self):
        for row in ({}, {"status": "reachable"}, {"status": "reachable", "delayMs": True}, {"status": "reachable", "delayMs": -1}, {"status": "PRIVATE_UNKNOWN"}):
            with self.subTest(row=row):
                self.assertEqual(qt_view.Window._status(row)[0], "未测试")

    def test_native_step_widgets_support_tab_and_keyboard_activation(self):
        events = []
        self.window.detectRequested.connect(lambda: events.append("detect"))
        self.window.generateRequested.connect(lambda: events.append("generate"))
        self.window.set_permissions(can_generate=True, can_apply=False, installed=False, output_available=False)
        self.window.detect_button.setFocus()
        self.assertEqual(self.window.detect_button.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        QTest.keyClick(self.window.detect_button, Qt.Key.Key_Return)
        QTest.keyClick(self.window.detect_button, Qt.Key.Key_Tab)
        self.assertTrue(self.window.generate_button.hasFocus())
        QTest.keyClick(self.window.generate_button, Qt.Key.Key_Space)
        self.assertEqual(events, ["detect", "generate"])

    def test_top_action_buttons_emit_once_and_third_step_is_readonly(self):
        events = []
        self.window.detectRequested.connect(lambda: events.append("detect"))
        self.window.generateRequested.connect(lambda: events.append("generate"))
        self.window.applyRequested.connect(lambda: events.append("apply"))
        self.window.set_permissions(True, True, True, True)
        QTest.mouseClick(self.window.detect_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.generate_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.apply_step, Qt.MouseButton.LeftButton)
        self.assertEqual(events, ["detect", "generate"])
        self.assertEqual(self.window.apply_step.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(self.window.apply_step.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertIsNone(self.window.apply_step.action_button)
        QTest.mouseClick(self.window.apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(events, ["detect", "generate", "apply"])

    def test_busy_locks_actions_paths_and_close_but_keeps_window_controls(self):
        self.window.set_permissions(True, True, True, True)
        self.window.set_busy(True)
        for widget in (self.window.detect_step, self.window.generate_step, self.window.apply_button, self.window.open_button, self.window.rollback_button, self.window.advanced_button, *self.window.paths.values()):
            self.assertFalse(widget.isEnabled())
        self.assertTrue(self.window.apply_step.isEnabled())
        self.assertTrue(self.window.minimize_button.isEnabled())
        self.assertTrue(self.window.maximize_button.isEnabled())
        with patch.object(self.window, "_message_dialog", return_value=False) as message:
            self.window.close()
            message.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.window.set_busy(False)
        self.assertTrue(self.window.generate_step.isEnabled())
        self.assertTrue(self.window.apply_button.isEnabled())

    def test_writeback_only_reports_live_when_verified(self):
        self.window.render_generate(generation(self.directory), checks())
        with self.assertRaises(ValueError):
            self.window.render_apply({"status": "prepared"})
        self.assertEqual(self.window.writeback_badge.text(), "待写回")
        self.window.render_apply({"status": "installed-and-live"})
        self.assertEqual(self.window.writeback_badge.text(), "已写回并生效")
        self.assertEqual(self.window.apply_step.circle.property("iconName"), "mdi6.check")
        self.assertEqual(self.window.apply_button.text(), "已写回并生效")
        self.assertTrue(self.window.apply_button.property("complete"))
        self.window.render_generate(generation(self.directory, needs_apply=False), checks())
        self.assertEqual(self.window.writeback_badge.text(), "已写回并生效")

    def test_completed_primary_button_stays_clear_while_disabled_and_after_busy(self):
        self.window.render_generate(generation(self.directory, needs_apply=False), checks())
        self.window.set_permissions(True, False, True, True)
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertEqual(self.window.apply_button.text(), "已写回并生效")
        self.assertEqual(self.window.apply_button.property("iconName"), "mdi6.check-circle-outline")
        self.application.processEvents()
        self.assertEqual(self.window.apply_button.grab().toImage().pixelColor(12, self.window.apply_button.height() // 2).name(), "#234b47")
        self.window.set_busy(True)
        self.assertEqual(self.window.apply_button.text(), "正在处理…")
        self.window.set_busy(False)
        self.assertEqual(self.window.apply_button.text(), "已写回并生效")
        self.window.render_generate(generation(self.directory, needs_apply=True), checks())
        self.window.set_permissions(True, True, True, True)
        self.assertFalse(self.window.apply_button.property("complete"))
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertEqual(self.window.apply_button.text(), "写回 Clash 扩展配置")

    def test_primary_stays_purple_before_ready_without_bypassing_prerequisites(self):
        for detected in (False, True):
            with self.subTest(detected=detected):
                if detected:
                    self.window.render_detect(context(self.directory))
                    self.window.set_permissions(True, False, False, False)
                self.application.processEvents()
                self.assertFalse(self.window.apply_button.isEnabled())
                self.assertFalse(self.window.apply_button.property("complete"))
                self.assertIn("请先完成探测环境和生成备份", self.window.apply_button.toolTip())
                color = self.window.apply_button.grab().toImage().pixelColor(12, 4)
                self.assertGreater(color.blue(), color.green() + 60)
                self.assertGreater(color.red(), color.green() + 20)
        self.window.set_busy(True)
        self.assertIn("等待当前任务", self.window.apply_button.toolTip())
        self.window.set_busy(False)
        self.window.render_generate(generation(self.directory, needs_apply=False), checks())
        self.window.set_permissions(True, False, True, True)
        self.assertIn("已校验生效", self.window.apply_button.toolTip())

    def test_file_rows_are_aligned_and_have_requested_placeholder_copy(self):
        self.assertEqual([label.text() for label in self.window._file_descriptions], ["BAK 文件", "端口映射表"])
        self.assertEqual(self.window.bak_field.placeholderText(), "用于 ZeroOmega 恢复")
        self.assertEqual(self.window.map_field.placeholderText(), "生成后显示端口映射表")
        for width, height in ((1200, 850), (1100, 760)):
            self.window.resize(width, height)
            self.application.processEvents()
            self.assertEqual(self.bounds(self.window.bak_field)[0], self.bounds(self.window.map_field)[0])
            self.assertEqual(self.bounds(self.window.bak_field)[2], self.bounds(self.window.map_field)[2])
            self.assertEqual(self.bounds(self.window.bak_copy_button)[0], self.bounds(self.window.map_copy_button)[0])
            self.assertEqual(self.window.bak_copy_button.height(), self.window.bak_field.height())
            self.assertEqual(self.window.map_copy_button.height(), self.window.map_field.height())

    def test_footer_is_one_line_with_history_in_tooltip(self):
        self.window.tell("探测成功", "公开提示")
        self.window.show_progress(3, 69)
        self.assertIn("3 / 69", self.window.status_label.text())
        self.assertIn("探测成功", self.window.status_label.toolTip())
        self.assertFalse(self.window.status_label.wordWrap())
        self.assertLessEqual(self.window.footer.height(), 40)

    def test_real_chinese_backup_name_and_full_path_are_available(self):
        self.window.render_generate(generation(self.directory), checks())
        self.application.processEvents()
        self.assertEqual(self.window.bak_field.text(), BAK_NAME)
        self.assertEqual(self.window.bak_field.toolTip(), str(self.directory / BAK_NAME))
        self.window.bak_copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), str(self.directory / BAK_NAME))
        for width, height in ((1380, 960), (1100, 760)):
            self.window.resize(width, height)
            self.application.processEvents()
            self.window._fit_file_text()
            self.assertLessEqual(QFontMetrics(self.window.bak_field.font()).horizontalAdvance(BAK_NAME), self.window.bak_field.width() - 18)

    def test_confirmation_states_private_backup_persistent_write_and_reload(self):
        with patch.object(self.window, "_message_dialog", return_value=False) as dialog:
            self.assertFalse(self.window.confirm_apply())
            title, message = dialog.call_args.args
            self.assertIn("写回 Clash", title)
            self.assertIn("私有备份", message)
            self.assertIn("持久扩展脚本", message)
            self.assertIn("热重载", message)
            self.assertIn("短暂中断", message)
            self.assertIn("不会向机场拉取订阅", message)
            self.assertTrue(dialog.call_args.kwargs["confirm"])

    def test_confirmation_dialog_defaults_to_cancel(self):
        def rejected(dialog):
            defaults = [button for button in dialog.findChildren(qt_view.QPushButton) if button.isDefault()]
            self.assertEqual(len(defaults), 1)
            self.assertEqual(defaults[0].text(), "取消")
            return QDialog.DialogCode.Rejected
        with patch.object(qt_view._DarkDialog, "exec", rejected):
            self.assertFalse(self.window.confirm_apply())

    def test_policy_review_requires_explicit_complete_allowed_choices(self):
        rows = [{"name": "混合组一", "choices": ["DIRECT", "PROXY"], "currentAction": "DIRECT"}, {"name": "混合组二", "choices": ["REJECT", "PROXY"], "currentAction": "PROXY"}]
        dialog = qt_view._PolicyDialog(self.window, rows)
        self.assertFalse(dialog.confirm_button.isEnabled())
        self.assertIsNone(dialog.choices())
        for combo in dialog.combos.values():
            self.assertEqual(combo.currentText(), "请选择")
            self.assertIsNone(combo.currentData())
            combo.setCurrentIndex(2)
        self.assertTrue(dialog.confirm_button.isEnabled())
        self.assertEqual(dialog.choices(), {"混合组一": "PROXY", "混合组二": "PROXY"})
        dialog.reject()
        with patch.object(qt_view._PolicyDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.assertIsNone(self.window.review_policies(rows))

    def test_policy_review_rejects_unknown_actions(self):
        with self.assertRaises(ValueError):
            qt_view._PolicyDialog(self.window, [{"name": "混合组", "choices": ["PRIVATE_ACTION"]}])


def snapshot(path: Path):
    application = QApplication.instance() or QApplication([])
    window = qt_view.Window()
    window.resize(1200, 850)
    window.show()
    application.processEvents()
    folder = Path("C:/Tools/ClashBackup")
    window.render_detect(context(folder))
    window.render_generate(generation(folder), checks())
    window.set_permissions(can_generate=True, can_apply=True, installed=True, output_available=True)
    application.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(path)):
        raise RuntimeError("Cannot save the synthetic Qt render")
    window.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(QtViewTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful() and args.snapshot:
        snapshot(args.snapshot)
    print(json.dumps({"suite": "qt-view-offscreen", "passed": result.testsRun - len(result.failures) - len(result.errors), "total": result.testsRun, "realApiCalls": 0, "backendAccess": False, "browserAccess": False, "visibleDesktopWindow": False, "syntheticSnapshot": bool(args.snapshot)}, ensure_ascii=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
