"""Widget interaction/layout checks using synthetic data and no network access."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if "--native-visual-demo" in sys.argv else "offscreen")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import view


def sources():
    return [
        {"id": "demo-one", "name": "订阅一 · 日常线路", "nodeCount": 69, "selected": True},
        {"id": "demo-two", "name": "飞鸟加速", "nodeCount": 108, "type": "feiniao", "selected": True},
        {"id": "demo-three", "name": "备用订阅", "nodeCount": 18},
        {"id": "demo-invalid", "name": "不可用的订阅", "available": False, "error": "配置中没有真实节点"},
    ]


def nodes(count=177, tested=False):
    names = ["香港800 专线 BGP", "香港300 中继 BGP", "日本中转811", "美国中转815", "台湾611 中继", "新加坡302 中继", "德国中转311", "英国中转310"]
    rows = []
    for index in range(count):
        row = {"name": names[index % len(names)] + (f" {index // len(names) + 1}" if index >= len(names) else ""),
               "source": "飞鸟加速" if index >= 69 else "订阅一", "sourceId": "demo-two" if index >= 69 else "demo-one",
               "port": 22000 + index, "status": "untested"}
        if tested:
            row.update(status="reachable", delayMs=59 + index % 81)
            if index % 11 == 3:
                row.update(status="timeout", delayMs=None)
            elif index % 17 == 4:
                row.update(status="failed", delayMs=None)
        rows.append(row)
    return rows


def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        for filename in ("msyh.ttc", "msyhbd.ttc"):
            font = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / filename
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
    view.configure_application(app)
    return app


class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.window = view.Window()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()

    def bounds(self, widget):
        point = widget.mapTo(self.window, QPoint(0, 0))
        return point.x(), point.y(), point.x() + widget.width(), point.y() + widget.height()

    def test_native_window_assets_and_disabled_initial_actions(self):
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.logo_source.isNull())
        for button in (self.window.refresh_button, self.window.start_button, self.window.export_button, self.window.test_button, self.window.stop_button):
            self.assertTrue(button.property("iconName").startswith("mdi6."))
            self.assertFalse(button.icon().isNull())
        self.assertTrue(self.window.refresh_button.isEnabled())
        for button in (self.window.prepare_button, self.window.start_button, self.window.test_button, self.window.stop_button, self.window.export_button, self.window.bak_copy):
            self.assertFalse(button.isEnabled())
        self.assertEqual("用于 ZeroOmega 恢复", self.window.bak_edit.placeholderText())

    def test_selected_sources_only_emit_and_unavailable_not_selected(self):
        events = []
        self.window.prepareRequested.connect(lambda values: events.append(values))
        self.window.set_sources(sources())
        self.assertEqual(["demo-one", "demo-two"], self.window.get_selected_sources())
        self.assertFalse(self.window._source_cards["demo-invalid"].checkbox.isEnabled())
        QTest.mouseClick(self.window.prepare_button, Qt.MouseButton.LeftButton)
        self.assertEqual([["demo-one", "demo-two"]], events)
        QTest.mouseClick(self.window._source_cards["demo-one"].checkbox, Qt.MouseButton.LeftButton)
        self.assertEqual(["demo-two"], self.window.get_selected_sources())
        self.assertEqual(1, len(events))

    def test_source_refresh_keeps_previous_explicit_selection(self):
        self.window.set_sources(sources())
        self.window.set_selected_sources(["demo-three"])
        self.window.set_sources(sources()[::-1])
        self.assertEqual(["demo-three"], self.window.get_selected_sources())
        self.window.set_selected_sources(["demo-invalid"])
        self.assertEqual([], self.window.get_selected_sources())

    def test_select_all_handles_partial_none_and_all(self):
        self.window.set_sources(sources())
        self.assertEqual(Qt.CheckState.PartiallyChecked, self.window.all_checkbox.checkState())
        QTest.mouseClick(self.window.all_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(9, self.window.all_checkbox.height() // 2))
        self.assertEqual(3, len(self.window.get_selected_sources()))
        QTest.mouseClick(self.window.all_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(9, self.window.all_checkbox.height() // 2))
        self.assertEqual([], self.window.get_selected_sources())

    def test_primary_starts_selection_without_required_preview_step(self):
        events = []
        self.window.startRequested.connect(lambda: events.append("start"))
        self.window.set_sources(sources())
        self.assertTrue(self.window.start_button.isEnabled())
        QTest.mouseClick(self.window.start_button, Qt.MouseButton.LeftButton)
        self.assertEqual(["start"], events)

    def test_busy_prevents_double_operations_and_restores_permissions(self):
        self.window.set_sources(sources())
        self.window.set_output_dir("C:/Demo/Backup")
        self.window.set_nodes(nodes(8))
        self.window.set_backend_status({"running": True, "prepared": True})
        self.window.set_busy(True, "正在检测节点")
        for button in (self.window.refresh_button, self.window.prepare_button, self.window.start_button, self.window.stop_button, self.window.test_button, self.window.export_button, self.window.hide_button, self.window.settings_button):
            self.assertFalse(button.isEnabled(), button.objectName())
        self.assertFalse(self.window._source_cards["demo-one"].checkbox.isEnabled())
        self.assertEqual("busy", self.window.backend_badge.property("phase"))
        self.window.set_busy(False)
        self.assertTrue(self.window.stop_button.isEnabled())
        self.assertTrue(self.window.test_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())

    def test_running_success_is_green_and_stop_retains_labelled_snapshot(self):
        self.window.set_sources(sources())
        self.window.set_nodes(nodes(3, tested=True))
        self.assertIn("上次可用", self.window.table.item(0, 3).text())
        self.window.set_backend_status({"running": True, "prepared": True})
        self.assertEqual("已运行", self.window.start_button.text())
        self.assertEqual("running", self.window.start_button.property("phase"))
        self.assertEqual("可用 · 59 ms", self.window.table.item(0, 3).text())
        self.assertEqual(view.GREEN, self.window.table.item(0, 3).foreground().color().name())
        self.window.set_backend_status({"running": False})
        self.assertIn("上次可用", self.window.table.item(0, 3).text())
        self.assertEqual(view.MUTED, self.window.table.item(0, 3).foreground().color().name())
        self.assertFalse(self.window.test_button.isEnabled())

    def test_search_filters_nodes_and_subscription_names(self):
        self.window.set_nodes(nodes(20))
        QTest.keyClicks(self.window.search_edit, "815")
        self.assertEqual(3, sum(not self.window.table.isRowHidden(index) for index in range(20)))
        self.window.search_edit.setText("不存在")
        self.assertTrue(all(self.window.table.isRowHidden(index) for index in range(20)))
        self.window.search_edit.clear()
        self.assertTrue(all(not self.window.table.isRowHidden(index) for index in range(20)))

    def test_export_full_paths_signals_and_placeholders(self):
        events = []
        self.window.copyPathRequested.connect(events.append)
        self.window.set_sources(sources())
        self.window.set_nodes(nodes(2))
        self.window.set_backend_status({"running": True, "prepared": True})
        self.window.set_output_dir("C:/Demo/Backup")
        bak = "C:/Demo/Backup/ZeroOmega-多订阅-手动选择.bak"
        mapping = "C:/Demo/Backup/节点与端口映射表.md"
        self.window.set_export_paths(bak, mapping)
        self.assertEqual(Path(bak).name, self.window.bak_edit.text())
        self.assertEqual(bak, self.window.bak_edit.toolTip())
        self.assertTrue(self.window.bak_copy.isEnabled())
        QTest.mouseClick(self.window.bak_copy, Qt.MouseButton.LeftButton)
        self.assertEqual([bak], events)
        self.assertEqual("complete", self.window.export_button.property("phase"))
        self.window.set_export_paths()
        self.assertFalse(self.window.bak_copy.isEnabled())
        self.assertEqual("", self.window.bak_edit.text())

    def test_all_persistent_controls_inside_default_and_minimum_window(self):
        self.window.set_sources(sources())
        self.window.set_nodes(nodes())
        self.window.set_output_dir("C:/Demo/Backup")
        for width, height in ((1200, 800), (1080, 720), (1000, 640)):
            with self.subTest(viewport=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual((width, height), (self.window.width(), self.window.height()))
                for widget in (self.window.header, self.window.summary, self.window.source_panel, self.window.node_panel, self.window.table, self.window.output_panel, self.window.start_button, self.window.stop_button, self.window.export_button, self.window.test_button, self.window.hide_button, self.window.bak_edit, self.window.footer):
                    left, top, right, bottom = self.bounds(widget)
                    self.assertGreaterEqual(left, 0, widget.objectName())
                    self.assertGreaterEqual(top, 0, widget.objectName())
                    self.assertLessEqual(right, width, widget.objectName())
                    self.assertLessEqual(bottom, height, widget.objectName())
                    self.assertTrue(widget.isVisible())
                self.assertLessEqual(self.window.table.horizontalScrollBar().maximum(), 0)
                if height >= 720:
                    self.assertGreaterEqual(self.window.table.viewport().height() // self.window.table.rowHeight(0), 8 if height == 800 else 6)
                else:
                    self.assertGreaterEqual(self.window.table.viewport().height() // self.window.table.rowHeight(0), 4)

    def test_duplicate_source_labels_remote_type_and_current_badge(self):
        self.window.set_sources([
            {"id": "a" * 60 + "01ab", "name": "xmrth1.net.yaml", "nodeCount": 69, "type": "remote"},
            {"id": "b" * 60 + "32cd", "name": "xmrth1.net.yaml", "nodeCount": 69, "type": "remote", "current": True},
            {"id": "bridge", "name": "飞鸟加速", "nodeCount": 108, "type": "local", "isFeiniao": True},
        ])
        self.app.processEvents()
        first = self.window._source_cards["a" * 60 + "01ab"]
        second = self.window._source_cards["b" * 60 + "32cd"]
        bridge = self.window._source_cards["bridge"]
        self.assertEqual("xmrth1.net.yaml · 01ab", first.name_label.text())
        self.assertEqual("xmrth1.net.yaml · 32cd", second.name_label.text())
        self.assertEqual("xmrth1.net.yaml", second.name_label.toolTip())
        self.assertIn("远程订阅", first.hint.text())
        self.assertIn("已下载", first.hint.toolTip())
        self.assertFalse(first.current_badge.isVisible())
        self.assertTrue(second.current_badge.isVisible())
        self.assertEqual("飞鸟加速", bridge.name_label.text())
        self.assertIn("飞鸟桥接", bridge.hint.text())

    def test_four_and_five_digit_delay_results_fit_status_column(self):
        self.window.set_backend_status({"running": True})
        self.window.set_nodes([{"name": "示例节点", "source": "示例订阅", "port": 46000, "status": "available", "delayMs": 1291}])
        self.window.resize(1080, 720)
        self.app.processEvents()
        metric = QFontMetrics(self.window.table.font())
        for result in ("可用 · 1291 ms", "可用 · 65535 ms", "上次可用 · 1291 ms"):
            self.assertLessEqual(metric.horizontalAdvance(result) + 40, self.window.table.columnWidth(3), result)

    def test_current_and_selected_sources_are_first_only_at_refresh(self):
        self.window.resize(1000, 640)
        self.window.set_sources([
            {"id": "unselected", "name": "未勾选订阅", "type": "remote", "nodeCount": 370},
            {"id": "duplicate", "name": "xmrth1.net.yaml", "type": "remote", "nodeCount": 69},
            {"id": "selected", "name": "飞鸟加速（108 节点，本地桥接）", "type": "local", "isFeiniao": True, "selected": True},
            {"id": "current", "name": "xmrth1.net.yaml", "type": "remote", "nodeCount": 69, "current": True, "selected": True},
        ])
        self.app.processEvents()
        order = list(self.window._source_cards)
        self.assertEqual(["current", "selected"], order[:2])
        selected_card = self.window._source_cards["selected"]
        card_top = selected_card.mapTo(self.window.source_scroll.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(card_top, 0)
        self.assertLessEqual(card_top + selected_card.height(), self.window.source_scroll.viewport().height())
        self.window.set_selected_sources(["unselected"])
        self.assertEqual(order, list(self.window._source_cards))

    def test_policy_settings_requires_every_explicit_choice(self):
        dialog = view.SettingsDialog(self.window, {"privateRoot": "C:/Demo/Private"}, [{"name": "其他流量", "key": "others"}, {"name": "混合规则", "key": "mixed"}])
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.policy_combos["others"].setCurrentIndex(1)
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.policy_combos["mixed"].setCurrentIndex(2)
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertEqual({"others": "PROXY", "mixed": "DIRECT"}, dialog.result_values()["policies"])
        self.assertEqual("C:/Demo/Private", dialog.result_values()["privateRoot"])
        dialog.deleteLater()

    def test_close_hands_control_to_controller_without_stopping_anything(self):
        events = []
        self.window.closeRequested.connect(lambda: events.append("close"))
        QTest.mouseClick(self.window.close_button, Qt.MouseButton.LeftButton)
        self.assertEqual(["close"], events)
        self.assertTrue(self.window.isVisible())

    def test_pending_changes_are_explicitly_clickable_and_controller_gated(self):
        events = []
        self.window.prepareRequested.connect(events.append)
        self.window.set_sources(sources())
        self.window.set_nodes(nodes(3))
        self.window.set_output_dir("C:/Demo/Backup")
        self.window.set_backend_status({"running": True, "prepared": True, "pendingChanges": False, "canStart": False, "canExport": True, "canTest": True})
        self.assertFalse(self.window.prepare_button.isEnabled())
        self.assertEqual("已运行", self.window.start_button.text())
        self.window.set_backend_status({"pendingChanges": True, "canStart": True, "canExport": False, "canTest": False})
        self.assertEqual("应用选择", self.window.prepare_button.text())
        self.assertTrue(self.window.prepare_button.isEnabled())
        self.assertEqual("pending", self.window.prepare_button.property("phase"))
        self.assertEqual("应用订阅选择", self.window.start_button.text())
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertIn("原选择", self.window.backend_hint.text())
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertFalse(self.window.test_button.isEnabled())
        QTest.mouseClick(self.window.prepare_button, Qt.MouseButton.LeftButton)
        self.assertEqual([["demo-one", "demo-two"]], events)
        self.window.set_backend_status({"canStart": False})
        self.assertFalse(self.window.prepare_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())

    def test_controller_chinese_results_and_feiniao_badge(self):
        self.window.set_sources([{"id": "bridge", "name": "飞鸟", "nodeCount": 108, "isFeiniao": True}])
        self.assertIn("飞鸟桥接", self.window._source_cards["bridge"].hint.text())
        self.window.set_backend_status({"running": True})
        self.window.set_nodes([
            {"name": "示例线路", "source": "飞鸟", "port": 22000, "status": "可用", "delayMs": 45},
            {"name": "失败线路", "source": "飞鸟", "port": 22001, "status": "测试失败"},
            {"name": "超时线路", "source": "飞鸟", "port": 22002, "status": "超时"},
        ])
        self.assertEqual("可用 · 45 ms", self.window.table.item(0, 3).text())
        self.assertEqual("测试失败", self.window.table.item(1, 3).text())
        self.assertEqual("超时", self.window.table.item(2, 3).text())

    def test_running_process_with_unverified_entries_never_appears_green(self):
        self.window.set_sources(sources())
        self.window.set_nodes(nodes(3))
        self.window.set_output_dir("C:/Demo/Backup")
        self.window.set_backend_status({"running": True, "error": True, "prepared": True,
            "allListenersPrivate": False, "canStart": False, "canTest": False, "canExport": False})
        self.assertEqual("入口状态异常", self.window.backend_badge.text())
        self.assertEqual("error", self.window.backend_badge.property("phase"))
        self.assertEqual("后台需检查", self.window.start_button.text())
        self.assertEqual("error", self.window.start_button.property("phase"))
        self.assertFalse(self.window.test_button.isEnabled())
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertTrue(self.window.stop_button.isEnabled())
        self.window.set_backend_status({"running": True, "error": False, "allListenersPrivate": True, "canTest": True, "canExport": True})
        self.assertEqual("后台运行中", self.window.backend_badge.text())
        self.assertEqual("已运行", self.window.start_button.text())
        self.assertEqual("running", self.window.start_button.property("phase"))


def visual_demo(directory: Path, native: bool = False) -> dict[str, Any]:
    app = application()
    directory.mkdir(parents=True, exist_ok=True)
    window = view.Window("0.1.0 DEMO")
    window.set_output_dir("C:/Demo/BrowserBackup")
    window.show()
    captures = []
    screen = window.screen()
    available = screen.availableGeometry() if screen else None
    sizes = ((window.width(), window.height()), (1000, 640)) if native else ((1200, 800), (1080, 720), (1000, 640))
    density = float(window.devicePixelRatioF())
    for state in ("initial", "selected", "prepared", "running", "pending", "exported", "busy", "stopped", "error"):
        window.set_busy(False)
        window.set_backend_status({"running": False, "prepared": False, "error": False, "pendingChanges": False, "canStart": True, "canTest": False, "canExport": False})
        window.set_export_paths()
        window.set_sources([] if state == "initial" else sources())
        if state != "initial":
            window.set_selected_sources(["demo-one", "demo-two"])
        window.set_nodes([] if state in ("initial", "selected") else nodes(tested=state in ("running", "pending", "exported", "stopped")))
        if state in ("prepared", "running", "pending", "exported", "busy", "stopped"):
            running = state in ("running", "pending", "exported", "busy")
            window.set_backend_status({"prepared": True, "running": running, "canTest": running, "canExport": running})
        if state == "pending":
            window.set_selected_sources(["demo-one"])
            window.set_backend_status({"pendingChanges": True, "canStart": True, "canTest": False, "canExport": False})
        if state == "exported":
            window.set_export_paths("C:/Demo/BrowserBackup/ZeroOmega-多订阅-手动选择.bak", "C:/Demo/BrowserBackup/节点与端口映射表.md")
        if state == "busy":
            window.set_busy(True, "正在检测节点")
        if state == "error":
            window.set_backend_status({"error": True})
        window.set_message(f"DEMO · 原生密度 {density:g}× · 不读取订阅或启动核心。" + (" 后台无法启动，请检查提示。" if state == "error" else ""), state == "error")
        for width, height in sizes:
            window.resize(width, height)
            app.processEvents()
            if native and available:
                window.move(available.x() + max(0, (available.width() - window.width()) // 2), available.y() + max(0, (available.height() - window.height()) // 2))
                QTest.qWait(180)
            name = f"hub-native-demo-{state}-{window.width()}x{window.height()}-dpr{density:g}.png" if native else f"hub-demo-{state}-{width}x{height}.png"
            path = directory / name
            capture = window.screen().grabWindow(int(window.winId())) if native else window.grab()
            if capture.isNull():
                raise ValueError("Qt capture unavailable")
            capture.save(str(path))
            captures.append(str(path))
    window.hide()
    window.deleteLater()
    app.processEvents()
    return {"captures": captures, "synthetic": True, "networkCalls": 0, "native": native,
            "density": density, "sizes": sizes, "available": [available.width(), available.height()] if available else None}


def compare_live(live_path: Path, destination: Path, reference_path: Path | None = None) -> dict[str, Any]:
    """Compose existing native captures for QA; no reconstructed UI artwork."""
    app = application()
    reference_path = reference_path or Path(__file__).resolve().parent.parent / "clash-zeroomega-app" / "design-qa-assets" / "qt-final-generated-comparison.png"
    reference = QImage(str(reference_path))
    live = QImage(str(live_path))
    if reference.isNull() or live.isNull():
        raise ValueError("QA source or live capture unavailable")
    if reference_path.name == "qt-final-generated-comparison.png":
        # The supplied comparison board has the approved actual 1200×850
        # assistant capture at left, below its 64 px caption. Preserve pixels.
        if reference.width() != 2432 or reference.height() != 918:
            raise ValueError("QA reference dimensions changed")
        source = reference.copy(0, 64, 1200, 850)
        source_crop = [0, 64, 1200, 850]
    else:
        source, source_crop = reference, None
    destination.mkdir(parents=True, exist_ok=True)
    source_path = destination / "hub-approved-original-style-source.png"
    source.save(str(source_path))
    comparison = QImage(source.width() + live.width() + 50, max(source.height(), live.height()) + 60, QImage.Format.Format_RGB32)
    comparison.fill(QColor("#101824"))
    painter = QPainter(comparison)
    painter.setPen(QColor(view.WHITE))
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(16)
    painter.setFont(font)
    painter.drawText(16, 28, f"已选原助手风格 · 原生 {source.width()}×{source.height()}")
    painter.drawText(source.width() + 34, 28, f"新多订阅助手 · 真实 {live.width()}×{live.height()} · 功能布局有意扩展")
    painter.drawImage(16, 44, source)
    painter.drawImage(source.width() + 34, 44, live)
    painter.end()
    full_path = destination / f"hub-style-live-comparison-{live.width()}x{live.height()}.png"
    comparison.save(str(full_path))
    # Detail reads remain at original pixel density, with no stretching.
    old_controls = source.copy(716, 445, 468, 356)
    new_controls = live.copy(18, live.height() - 244, live.width() - 36, 208)
    focused = QImage(old_controls.width() + new_controls.width() + 50, max(old_controls.height(), new_controls.height()) + 60, QImage.Format.Format_RGB32)
    focused.fill(QColor("#101824"))
    painter = QPainter(focused)
    painter.setPen(QColor(view.WHITE))
    painter.setFont(font)
    painter.drawText(16, 28, "原助手：备份文件与完成状态")
    painter.drawText(old_controls.width() + 34, 28, "新助手：备份、后台、检测与完成状态（1:1 原生像素）")
    painter.drawImage(16, 44, old_controls)
    painter.drawImage(old_controls.width() + 34, 44, new_controls)
    painter.end()
    focused_path = destination / f"hub-style-controls-comparison-{live.width()}x{live.height()}.png"
    focused.save(str(focused_path))
    result = {"reference": str(reference_path.resolve()), "referenceCrop": source_crop,
              "source": str(source_path.resolve()), "live": str(live_path.resolve()),
              "sourcePixels": [source.width(), source.height()], "livePixels": [live.width(), live.height()],
              "fullComparison": str(full_path.resolve()), "focusedComparison": str(focused_path.resolve()),
              "rescaled": False, "layoutIntentionallyExtended": True}
    (destination / f"hub-style-live-comparison-{live.width()}x{live.height()}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--visual-demo", type=Path)
    parser.add_argument("--native-visual-demo", type=Path)
    parser.add_argument("--compare-live", type=Path)
    parser.add_argument("--comparison-output", type=Path)
    arguments, remaining = parser.parse_known_args()
    if arguments.native_visual_demo:
        print(json.dumps(visual_demo(arguments.native_visual_demo, native=True), ensure_ascii=False))
    elif arguments.compare_live:
        print(json.dumps(compare_live(arguments.compare_live, arguments.comparison_output or Path(__file__).resolve().parent / "design-qa-assets"), ensure_ascii=False))
    elif arguments.visual_demo:
        print(json.dumps(visual_demo(arguments.visual_demo), ensure_ascii=False))
    else:
        unittest.main(argv=[sys.argv[0], *remaining])
