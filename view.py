"""Native Qt presentation for the independent subscription hub.

This module owns widgets only. It never reads subscriptions or starts a core.
"""
from __future__ import annotations

from pathlib import Path
from collections import Counter
import re
import sys
from typing import Any

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizeGrip, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
import qtawesome as qta
from qt_view import _TitleBar as _OriginalTitleBar

BG, CARD, FIELD = "#141e2b", "#1c2939", "#142130"
LINE, WHITE, MUTED = "#344a65", "#f1f5fd", "#afbed4"
BLUE, PURPLE, GREEN, RED, YELLOW = "#7bb8ff", "#ab91ff", "#59e2a0", "#ff7181", "#ffce67"


def _text(value: Any, fallback: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return fallback
    return "".join(char for char in str(value) if ord(char) >= 32).strip() or fallback


def _icon(name: str, color: str = BLUE) -> QIcon:
    return qta.icon(name.replace("mdi.", "mdi6.", 1), color=color, color_disabled="#65758d")


def _button(text: str, name: str, icon: str = "", tooltip: str = "") -> QPushButton:
    result = QPushButton(text)
    result.setObjectName(name)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.setMinimumHeight(36)
    result.setAccessibleName(text or tooltip)
    if tooltip:
        result.setToolTip(tooltip)
    if icon:
        result.setIcon(_icon(icon))
        result.setIconSize(QSize(20, 20))
        result.setProperty("iconName", icon)
    return result


def _phase(widget: QWidget, value: str) -> None:
    widget.setProperty("phase", value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _TitleBar(_OriginalTitleBar):
    """Reuse the copied assistant's native drag/maximize behavior."""


class _SourceCard(QFrame):
    toggled = Signal()

    def __init__(self, source: dict[str, Any], checked: bool, display_suffix: str = "") -> None:
        super().__init__()
        self.setObjectName("SourceCard")
        self.source_id = _text(source.get("id", source.get("key", source.get("uid"))))
        available = source.get("available", True) is not False
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 11, 12, 11)
        row.setSpacing(10)
        self.checkbox = QCheckBox()
        self.checkbox.setObjectName("SourceCheckbox")
        self.checkbox.setAccessibleName("选择订阅 " + _text(source.get("name"), "未命名订阅"))
        self.checkbox.setEnabled(available)
        self.checkbox.setChecked(checked and available)
        self.checkbox.toggled.connect(self._toggle)
        row.addWidget(self.checkbox)
        words = QVBoxLayout()
        words.setSpacing(4)
        original_name = _text(source.get("name"), "未命名订阅")
        self.name_label = QLabel(original_name + display_suffix)
        self.name_label.setObjectName("SourceName")
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_label.setWordWrap(True)
        self.name_label.setMinimumWidth(0)
        self.name_label.setToolTip(original_name)
        source_heading = QHBoxLayout()
        source_heading.setSpacing(5)
        source_heading.addWidget(self.name_label, 1)
        self.current_badge = QLabel("当前")
        self.current_badge.setObjectName("CurrentBadge")
        self.current_badge.setToolTip("电脑 Clash 当前启用的配置；浏览器使用哪些订阅由勾选决定。")
        self.current_badge.setVisible(bool(source.get("current")))
        source_heading.addWidget(self.current_badge, 0, Qt.AlignmentFlag.AlignTop)
        words.addLayout(source_heading)
        count = source.get("nodeCount", source.get("count", 0))
        kind = _text(source.get("type", source.get("kind")), "")
        is_bridge = kind.lower() in ("feiniao", "flybird", "bridge") or bool(source.get("isBridge")) or bool(source.get("isFeiniao"))
        label = "飞鸟桥接" if is_bridge else "远程订阅" if kind.lower() == "remote" else "本地订阅"
        hint = _text(source.get("error"), "无法读取") if not available else f"{count} 个节点 · {label}"
        self.hint = QLabel(hint)
        self.hint.setObjectName("SourceHint")
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setWordWrap(True)
        self.hint.setToolTip(hint + ("\n节点读取自 Clash 已下载的本地配置；刷新不会向机场更新订阅。" if kind.lower() == "remote" else ""))
        words.addWidget(self.hint)
        row.addLayout(words, 1)
        self.available = available
        self._toggle()

    def _toggle(self, *_args) -> None:
        _phase(self, "selected" if self.checkbox.isChecked() else "unavailable" if not self.available else "idle")
        self.toggled.emit()

    def set_compact(self, compact: bool) -> None:
        self.layout().setContentsMargins(12, 8 if compact else 11, 12, 8 if compact else 11)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.checkbox.isEnabled():
            self.checkbox.toggle()
            event.accept()
        else:
            super().mousePressEvent(event)


class SettingsDialog(QDialog):
    """Optional paths and unresolved rule decisions, returned to the controller."""
    def __init__(self, parent: QWidget, values: dict[str, Any] | None = None,
                 policy_issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("后台设置")
        self.setWindowIcon(parent.windowIcon())
        self.setMinimumWidth(680)
        self.setMaximumHeight(700)
        self.values = dict(values or {})
        self.fields: dict[str, QLineEdit] = {}
        self.policy_combos: dict[str, QComboBox] = {}
        self.setStyleSheet(parent.styleSheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 20)
        outer.setSpacing(14)
        heading = QLabel("后台设置")
        heading.setObjectName("SectionTitle")
        outer.addWidget(heading)
        description = QLabel("路径留空时自动探测。订阅更新请在 Clash Verge 中完成，然后返回刷新。")
        description.setObjectName("Muted")
        description.setWordWrap(True)
        outer.addWidget(description)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 6, 0)
        form.setSpacing(12)
        for key, caption, is_file in (
            ("privateRoot", "私有数据目录", False),
            ("clashData", "Clash 配置目录", False),
            ("bridgeRoot", "飞鸟桥接目录", False),
            ("corePath", "Mihomo 核心程序", True),
        ):
            form.addWidget(QLabel(caption))
            row = QHBoxLayout()
            edit = QLineEdit(_text(self.values.get(key)))
            edit.setPlaceholderText("自动探测")
            edit.setMinimumWidth(0)
            edit.setAccessibleName(caption)
            self.fields[key] = edit
            row.addWidget(edit, 1)
            browse = _button("选择", "BrowseButton", "mdi6.folder-outline")
            browse.clicked.connect(lambda _checked=False, target=edit, files=is_file: self._browse(target, files))
            row.addWidget(browse)
            form.addLayout(row)
        issues = policy_issues or []
        if issues:
            form.addSpacing(6)
            heading = QLabel("需要确认的分流动作")
            heading.setObjectName("Subheading")
            form.addWidget(heading)
            for issue in issues:
                key = _text(issue.get("key", issue.get("target", issue.get("name"))))
                if not key:
                    continue
                label = QLabel(_text(issue.get("label", issue.get("name", issue.get("target"))), key))
                label.setWordWrap(True)
                form.addWidget(label)
                combo = QComboBox()
                combo.addItem("请选择分流动作", None)
                choices = issue.get("choices", ["PROXY", "DIRECT", "REJECT"])
                for action, caption in (("PROXY", "使用浏览器所选节点"), ("DIRECT", "直连"), ("REJECT", "拦截")):
                    if action in choices:
                        combo.addItem(caption, action)
                saved = self.values.get("policies", {}).get(key)
                if saved:
                    combo.setCurrentIndex(max(0, combo.findData(saved)))
                combo.currentIndexChanged.connect(self._validate)
                self.policy_combos[key] = combo
                form.addWidget(combo)
        form.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = _button("取消", "Secondary")
        cancel.clicked.connect(self.reject)
        self.save_button = _button("保存设置", "Primary", "mdi6.content-save-outline")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)
        self.resize(720, min(680, 490 + len(issues) * 68))
        self._validate()

    def _browse(self, edit: QLineEdit, files: bool) -> None:
        path = QFileDialog.getOpenFileName(self, "选择核心程序", edit.text(), "应用程序 (*.exe);;全部文件 (*)")[0] if files else QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
        if path:
            edit.setText(path)

    def _validate(self, *_args) -> None:
        self.save_button.setEnabled(all(combo.currentData() in ("PROXY", "DIRECT", "REJECT") for combo in self.policy_combos.values()))

    def result_values(self) -> dict[str, Any]:
        return {**self.values, **{key: edit.text().strip() for key, edit in self.fields.items()},
                "policies": {**(self.values.get("policies") or {}), **{key: combo.currentData() for key, combo in self.policy_combos.items()}}}


class Window(QMainWindow):
    refreshRequested = Signal()
    prepareRequested = Signal(list)
    startRequested = Signal()
    stopRequested = Signal()
    testRequested = Signal()
    exportRequested = Signal(str)
    outputBrowseRequested = Signal()
    settingsRequested = Signal()
    hideRequested = Signal()
    copyPathRequested = Signal(str)
    openOutputRequested = Signal()
    selectionChanged = Signal(list)
    closeRequested = Signal()

    def __init__(self, version: str = "0.1.0", resources: Path | str | None = None) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resources = Path(resources or getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        self._busy = False
        self._status: dict[str, Any] = {"running": False, "prepared": False}
        self._sources: list[dict[str, Any]] = []
        self._nodes: list[dict[str, Any]] = []
        self._source_cards: dict[str, _SourceCard] = {}
        self._paths = {"bakPath": "", "mapPath": ""}
        self._compact = None
        self.setWindowTitle("多订阅后台助手 · " + version)
        self.setWindowIcon(QIcon(str(self.resources / "assets" / "app.ico")))
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen and QApplication.platformName() != "offscreen" else None
        minimum_width = min(1000, max(860, available.width() - 24)) if available else 1000
        minimum_height = min(640, max(580, available.height() - 24)) if available else 640
        self.setMinimumSize(minimum_width, minimum_height)
        self.resize(max(minimum_width, min(1200, available.width() - 24)) if available else 1200,
                    max(minimum_height, min(800, available.height() - 24)) if available else 800)
        self._build_ui(version)
        self._style()
        self._apply_density(self.height() < 700)
        self._refresh_permissions()

    def _build_ui(self, version: str) -> None:
        self.surface = QFrame()
        self.surface.setObjectName("AppSurface")
        self.setCentralWidget(self.surface)
        outer = QVBoxLayout(self.surface)
        self.outer = outer
        outer.setContentsMargins(20, 14, 20, 10)
        outer.setSpacing(10)
        self.header = _TitleBar()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(16)
        self.logo = QLabel()
        self.logo_source = QPixmap(str(self.resources / "assets" / "app-icon-64.png"))
        self.logo.setPixmap(self.logo_source.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.logo.setFixedSize(54, 54)
        header.addWidget(self.logo)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title = QLabel("多订阅后台助手")
        self.title.setObjectName("ProductTitle")
        titles.addWidget(self.title)
        subtitle = QLabel("多个订阅同时使用 · 浏览器独立选线")
        subtitle.setObjectName("Subtitle")
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.settings_button = _button("设置", "Secondary", "mdi6.cog-outline")
        self.settings_button.clicked.connect(self.settingsRequested)
        header.addWidget(self.settings_button)
        controls = QVBoxLayout()
        buttons = QHBoxLayout()
        self.minimize_button = _button("", "WindowControl", "mdi6.window-minimize", "最小化")
        self.maximize_button = _button("", "WindowControl", "mdi6.window-maximize", "最大化或还原")
        self.close_button = _button("", "CloseControl", "mdi6.close", "关闭窗口")
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setFixedSize(30, 30)
            buttons.addWidget(button)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button.clicked.connect(lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        self.close_button.clicked.connect(self.close)
        controls.addLayout(buttons)
        version_label = QLabel("v" + version)
        version_label.setObjectName("TinyMuted")
        version_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        controls.addWidget(version_label)
        header.addLayout(controls)
        outer.addWidget(self.header)

        self.summary = QFrame()
        self.summary.setObjectName("Summary")
        metrics = QHBoxLayout(self.summary)
        metrics.setContentsMargins(18, 6, 18, 6)
        metrics.setSpacing(26)
        self.source_metric = self._metric(metrics, "发现订阅", "0")
        self.selected_metric = self._metric(metrics, "已选订阅", "0")
        self.node_metric = self._metric(metrics, "加载节点", "0")
        metrics.addStretch()
        status_words = QVBoxLayout()
        status_words.setSpacing(3)
        self.backend_badge = QLabel("后台未运行")
        self.backend_badge.setObjectName("BackendBadge")
        self.backend_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.backend_hint = QLabel("先刷新订阅，再加载所选节点")
        self.backend_hint.setObjectName("Muted")
        status_words.addWidget(self.backend_badge, 0, Qt.AlignmentFlag.AlignRight)
        status_words.addWidget(self.backend_hint)
        metrics.addLayout(status_words)
        outer.addWidget(self.summary)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        self.source_panel = QFrame()
        self.source_panel.setObjectName("Card")
        self.source_panel.setFixedWidth(282)
        source_layout = QVBoxLayout(self.source_panel)
        self.source_layout = source_layout
        source_layout.setContentsMargins(14, 14, 14, 14)
        source_layout.setSpacing(10)
        source_head = QHBoxLayout()
        source_title = QLabel("订阅来源")
        source_title.setObjectName("SectionTitle")
        source_head.addWidget(source_title, 1)
        self.refresh_button = _button("刷新", "SmallAction", "mdi6.refresh", "读取 Clash 中已有的本地订阅；不向机场请求更新")
        self.refresh_button.setMinimumHeight(30)
        source_head.addWidget(self.refresh_button)
        self.refresh_button.clicked.connect(self.refreshRequested)
        source_layout.addLayout(source_head)
        self.all_checkbox = QCheckBox("全选")
        self.all_checkbox.setAccessibleName("全选可用订阅")
        self.all_checkbox.setToolTip("选择或取消所有可用订阅。")
        self.all_checkbox.setTristate(True)
        self.all_checkbox.clicked.connect(self._select_all)
        source_head.insertWidget(1, self.all_checkbox)
        self.source_scroll = QScrollArea()
        self.source_scroll.setWidgetResizable(True)
        self.source_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.source_content = QWidget()
        self.source_cards_layout = QVBoxLayout(self.source_content)
        self.source_cards_layout.setContentsMargins(0, 0, 5, 0)
        self.source_cards_layout.setSpacing(8)
        self.source_empty = QLabel("还没有读取订阅\n点击“刷新”开始")
        self.source_empty.setObjectName("Muted")
        self.source_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_cards_layout.addWidget(self.source_empty)
        self.source_cards_layout.addStretch()
        self.source_scroll.setWidget(self.source_content)
        source_layout.addWidget(self.source_scroll, 1)
        self.prepare_button = _button("加载选中订阅", "PrepareButton", "mdi6.layers-outline")
        self.prepare_button.clicked.connect(lambda: self.prepareRequested.emit(self.get_selected_sources()))
        source_layout.addWidget(self.prepare_button)
        hint = QLabel("在 Clash 更新订阅后，回来刷新。\n电脑当前订阅保持不变。")
        self.source_help = hint
        hint.setWordWrap(True)
        hint.setObjectName("TinyMuted")
        source_layout.addWidget(hint)
        middle.addWidget(self.source_panel)

        self.node_panel = QFrame()
        self.node_panel.setObjectName("Card")
        node_layout = QVBoxLayout(self.node_panel)
        node_layout.setContentsMargins(14, 12, 14, 10)
        node_layout.setSpacing(8)
        node_head = QHBoxLayout()
        self.node_title = QLabel("节点列表")
        self.node_title.setObjectName("SectionTitle")
        node_head.addWidget(self.node_title, 1)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("Search")
        self.search_edit.setPlaceholderText("搜索节点或订阅")
        self.search_edit.setAccessibleName("搜索节点或订阅")
        self.search_edit.setMaximumWidth(230)
        self.search_edit.addAction(_icon("mdi6.magnify"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.textChanged.connect(self._filter_rows)
        node_head.addWidget(self.search_edit)
        node_layout.addLayout(node_head)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["节点名称", "订阅", "本机端口", "连通性"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 112)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 190)
        self.table.setMinimumHeight(130)
        self.table.setAccessibleName("节点、订阅、本机端口与连通性列表")
        node_layout.addWidget(self.table, 1)
        self.node_hint = QLabel("加载后分配固定端口；不会自动选择或切换节点。")
        self.node_hint.setObjectName("TinyMuted")
        node_layout.addWidget(self.node_hint)
        middle.addWidget(self.node_panel, 1)
        outer.addLayout(middle, 1)

        self.output_panel = QFrame()
        self.output_panel.setObjectName("Card")
        self.output_panel.setFixedHeight(190)
        output = QHBoxLayout(self.output_panel)
        output.setContentsMargins(16, 13, 16, 13)
        output.setSpacing(22)
        files = QVBoxLayout()
        files.setSpacing(8)
        heading = QLabel("浏览器备份")
        heading.setObjectName("Subheading")
        heading.setMaximumHeight(23)
        files.addWidget(heading)
        directory_row = QHBoxLayout()
        directory_row.setSpacing(9)
        directory_label = QLabel("输出目录")
        directory_label.setFixedWidth(66)
        directory_row.addWidget(directory_label)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择备份保存位置")
        self.output_edit.setAccessibleName("输出目录")
        self.output_edit.setMinimumWidth(0)
        directory_row.addWidget(self.output_edit, 1)
        self.browse_button = _button("选择", "BrowseButton", "mdi6.folder-outline")
        self.browse_button.clicked.connect(self.outputBrowseRequested)
        directory_row.addWidget(self.browse_button)
        files.addLayout(directory_row)
        self.bak_edit, self.bak_copy = self._file_row(files, "BAK 文件", "用于 ZeroOmega 恢复", "bakPath")
        self.map_edit, self.map_copy = self._file_row(files, "端口映射", "生成后显示节点与端口映射表", "mapPath")
        output.addLayout(files, 1)
        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.start_button = _button("启动浏览器后台", "Primary", "mdi6.play-circle-outline")
        self.start_button.setMinimumHeight(44)
        self.start_button.clicked.connect(self.startRequested)
        actions.addWidget(self.start_button)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.export_button = _button("生成合并 BAK", "ExportButton", "mdi6.file-export-outline")
        self.export_button.clicked.connect(lambda: self.exportRequested.emit(self.get_output_dir()))
        self.test_button = _button("检测节点", "Secondary", "mdi6.lan-check")
        self.test_button.clicked.connect(self.testRequested)
        row.addWidget(self.export_button)
        row.addWidget(self.test_button)
        actions.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.stop_button = _button("停止后台", "Secondary", "mdi6.stop-circle-outline")
        self.stop_button.clicked.connect(self.stopRequested)
        self.hide_button = _button("隐藏到托盘", "Secondary", "mdi6.arrow-collapse-down")
        self.hide_button.clicked.connect(self.hideRequested)
        row.addWidget(self.stop_button)
        row.addWidget(self.hide_button)
        actions.addLayout(row)
        self.open_button = _button("打开输出目录", "SmallAction", "mdi6.folder-open-outline")
        self.open_button.setMinimumHeight(28)
        self.open_button.setStyleSheet("min-height: 20px; padding: 2px 8px;")
        self.open_button.clicked.connect(self.openOutputRequested)
        actions.addWidget(self.open_button)
        action_widget = QWidget()
        action_widget.setLayout(actions)
        action_widget.setFixedWidth(330)
        output.addWidget(action_widget)
        outer.addWidget(self.output_panel)
        self.output_edit.textChanged.connect(lambda _text: self._refresh_permissions())

        self.footer = QFrame()
        self.footer.setObjectName("Footer")
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(0, 5, 0, 0)
        foot.setSpacing(8)
        self.message_icon = QLabel()
        self.message_icon.setPixmap(_icon("mdi6.information-outline").pixmap(18, 18))
        foot.addWidget(self.message_icon)
        self.message_label = QLabel("先刷新订阅，再选择供浏览器使用的线路。")
        self.message_label.setObjectName("Message")
        self.message_label.setMinimumWidth(0)
        foot.addWidget(self.message_label, 1)
        self.ready_label = QLabel("准备就绪")
        self.ready_label.setObjectName("TinyMuted")
        foot.addWidget(self.ready_label)
        grip = QSizeGrip(self)
        grip.setFixedSize(12, 12)
        foot.addWidget(grip)
        outer.addWidget(self.footer)

    def _metric(self, layout: QHBoxLayout, caption: str, value: str) -> QLabel:
        box = QHBoxLayout()
        box.setSpacing(9)
        number = QLabel(value)
        number.setObjectName("MetricValue")
        title = QLabel(caption)
        title.setObjectName("Muted")
        box.addWidget(number)
        box.addWidget(title)
        layout.addLayout(box)
        return number

    def _file_row(self, layout: QVBoxLayout, caption: str, placeholder: str, key: str) -> tuple[QLineEdit, QPushButton]:
        row = QHBoxLayout()
        row.setSpacing(9)
        label = QLabel(caption)
        label.setFixedWidth(66)
        row.addWidget(label)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(placeholder)
        edit.setAccessibleName(caption)
        edit.setMinimumWidth(0)
        row.addWidget(edit, 1)
        copy = _button("", "CopyButton", "mdi6.content-copy", "复制完整文件路径")
        copy.setFixedSize(34, 34)
        copy.clicked.connect(lambda: self.copyPathRequested.emit(self._paths[key]))
        row.addWidget(copy)
        layout.addLayout(row)
        return edit, copy

    def _style(self) -> None:
        self.setStyleSheet(f"""
        QWidget {{ font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 13px; color: {WHITE}; }}
        QMainWindow, QDialog {{ background: {BG}; }}
        QFrame#AppSurface {{ background: {BG}; border: 1px solid {LINE}; border-radius: 12px; }}
        QFrame#Card, QFrame#Summary {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 11px; }}
        QLabel {{ background: transparent; border: none; }}
        QLabel#ProductTitle {{ font-size: 29px; font-weight: 700; }}
        QLabel#Subtitle {{ font-size: 14px; color: {MUTED}; }}
        QLabel#SectionTitle {{ font-size: 19px; font-weight: 700; }}
        QLabel#Subheading {{ font-size: 15px; font-weight: 600; }}
        QLabel#MetricValue {{ font-size: 25px; font-weight: 600; color: {BLUE}; }}
        QLabel#Muted, QLabel#SourceHint {{ color: {MUTED}; }}
        QLabel#SourceHint {{ font-size: 12px; }}
        QLabel#CurrentBadge {{ color: #c7ddff; background: #294767; border: 1px solid #6697cb; border-radius: 4px; padding: 1px 4px; font-size: 10px; }}
        QLabel#TinyMuted {{ font-size: 12px; color: {MUTED}; }}
        QLabel#BackendBadge {{ padding: 5px 13px; border: 1px solid {LINE}; border-radius: 13px; color: {MUTED}; background: {FIELD}; font-size: 13px; }}
        QLabel#BackendBadge[phase='running'] {{ border-color: {GREEN}; color: {GREEN}; background: #193d37; }}
        QLabel#BackendBadge[phase='prepared'] {{ border-color: {PURPLE}; color: #c7b6ff; background: #302d4c; }}
        QLabel#BackendBadge[phase='busy'] {{ border-color: {YELLOW}; color: {YELLOW}; background: #3a3427; }}
        QLabel#BackendBadge[phase='error'] {{ border-color: {RED}; color: {RED}; background: #3e2734; }}
        QPushButton {{ background: #24374d; border: 1px solid #53739b; border-radius: 7px; padding: 5px 10px; font-weight: 500; }}
        QPushButton:hover {{ border-color: {BLUE}; background: #2a4260; }}
        QPushButton:pressed {{ background: #345477; }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid {BLUE}; }}
        QPushButton:disabled {{ color: #77869c; background: #243043; border-color: #394b63; }}
        QPushButton#Primary {{ background: #6b50ef; border: 1px solid #b29dff; font-size: 17px; font-weight: 600; }}
        QPushButton#Primary:hover {{ background: #7c64fa; border-color: #d2c5ff; }}
        QPushButton#Primary:pressed {{ background: #5b3dd4; }}
        QPushButton#Primary:disabled {{ color: #9bafdc; background: #343e65; border-color: #56638b; }}
        QPushButton#Primary[phase='running'], QPushButton#Primary[phase='running']:disabled {{ background: #204b42; color: {GREEN}; border: 1px solid #55b992; }}
        QPushButton#Primary[phase='pending'] {{ background: #6b50ef; border-color: #b29dff; color: {WHITE}; }}
        QPushButton#Primary[phase='error'], QPushButton#Primary[phase='error']:disabled {{ background: #432c36; color: {RED}; border-color: {RED}; }}
        QPushButton#PrepareButton {{ background: #28446a; border-color: #7396cb; color: #dceaff; }}
        QPushButton#PrepareButton[phase='pending'] {{ background: #5945bc; border-color: #b29dff; color: {WHITE}; }}
        QPushButton#PrepareButton[phase='pending']:hover {{ background: #6a56ce; }}
        QPushButton#PrepareButton:disabled {{ background: #243043; border-color: #394b63; color: #77869c; }}
        QPushButton#ExportButton[phase='complete'] {{ background: #204b42; color: {GREEN}; border-color: #55b992; }}
        QPushButton#SmallAction {{ min-height: 25px; padding: 3px 8px; background: #203248; color: #b4d2ff; }}
        QPushButton#WindowControl, QPushButton#CloseControl {{ background: transparent; border: none; padding: 2px; }}
        QPushButton#WindowControl:hover {{ background: #2c3e54; }}
        QPushButton#CloseControl:hover {{ background: #81394c; }}
        QLineEdit, QComboBox {{ min-height: 26px; padding: 3px 9px; border: 1px solid #577396; border-radius: 6px; background: {FIELD}; selection-background-color: #4963a0; }}
        QLineEdit:disabled, QComboBox:disabled {{ color: #77869c; border-color: #394b63; }}
        QLineEdit#Search {{ min-height: 25px; }}
        QComboBox QAbstractItemView {{ background: {CARD}; color: {WHITE}; selection-background-color: #395987; }}
        QCheckBox {{ spacing: 9px; background: transparent; }}
        QCheckBox::indicator {{ width: 17px; height: 17px; }}
        QFrame#SourceCard {{ background: #182536; border: 1px solid #354861; border-radius: 8px; }}
        QFrame#SourceCard[phase='selected'] {{ background: #223b56; border-color: #73a2dd; }}
        QFrame#SourceCard[phase='unavailable'] {{ background: #212734; border-color: #493943; }}
        QLabel#SourceName {{ font-weight: 600; font-size: 14px; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
        QTableWidget {{ background: #172435; alternate-background-color: #1d2b3d; border: 1px solid {LINE}; selection-background-color: #2f4869; gridline-color: {LINE}; font-size: 15px; }}
        QTableWidget::item {{ padding: 0 8px; border-bottom: 1px solid #2b3d54; }}
        QHeaderView::section {{ background: #293c54; border: none; border-right: 1px solid #3c4d65; padding: 7px 8px; color: #e0ebfe; font-weight: 500; }}
        QScrollBar:vertical {{ background: #162335; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: #526780; min-height: 28px; border-radius: 4px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QScrollBar:horizontal {{ background: #162335; height: 9px; }}
        QScrollBar::handle:horizontal {{ background: #526780; min-width: 25px; border-radius: 4px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QFrame#Footer {{ border-top: 1px solid {LINE}; }}
        QToolTip {{ background: #24374d; color: {WHITE}; border: 1px solid #6484ae; padding: 6px; }}
        """)

    def set_sources(self, sources: list[dict[str, Any]]) -> None:
        previous = set(self.get_selected_sources())
        known = set(self._source_cards)
        self._sources = [dict(source) for source in sources if isinstance(source, dict)]
        while self.source_cards_layout.count():
            item = self.source_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._source_cards = {}
        self.source_empty = QLabel("没有找到可读取的订阅\n请检查 Clash 配置目录")
        self.source_empty.setObjectName("Muted")
        self.source_empty.setWordWrap(True)
        self.source_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_empty.setVisible(not self._sources)
        self.source_cards_layout.addWidget(self.source_empty)
        name_counts = Counter(_text(source.get("name"), "未命名订阅") for source in self._sources)
        def display_order(source: dict[str, Any]) -> int:
            source_id = _text(source.get("id", source.get("key", source.get("uid"))))
            selected = source_id in previous if source_id in known else bool(source.get("selected"))
            return 0 if source.get("current") else 1 if selected else 2
        for source in sorted(self._sources, key=display_order):
            source_id = _text(source.get("id", source.get("key", source.get("uid"))))
            if not source_id or source_id in self._source_cards:
                continue
            checked = source_id in previous if source_id in known else bool(source.get("selected", False))
            display_suffix = ""
            if name_counts[_text(source.get("name"), "未命名订阅")] > 1:
                # Backend identifiers are opaque hashes. Never put a raw URL
                # or unexpected credential-bearing identifier into a label.
                if re.fullmatch(r"[A-Za-z0-9_-]{4,128}", source_id):
                    display_suffix = " · " + source_id[-4:]
            card = _SourceCard(source, checked, display_suffix)
            card.set_compact(bool(self._compact))
            card.toggled.connect(self._selection_changed)
            self._source_cards[source_id] = card
            self.source_cards_layout.addWidget(card)
        self.source_cards_layout.addStretch()
        self.source_metric.setText(str(len(self._source_cards)))
        self._selection_changed()

    def _select_all(self, checked: bool) -> None:
        # A partially selected set advances to all selected, then to none.
        available = [card for card in self._source_cards.values() if card.available]
        target = not all(card.checkbox.isChecked() for card in available)
        for card in available:
            blocker = QSignalBlocker(card.checkbox)
            card.checkbox.setChecked(target)
            del blocker
            _phase(card, "selected" if target else "idle")
        self._selection_changed()

    def _selection_changed(self) -> None:
        selected = self.get_selected_sources()
        self.selected_metric.setText(str(len(selected)))
        available_count = sum(card.available for card in self._source_cards.values())
        blocker = QSignalBlocker(self.all_checkbox)
        self.all_checkbox.setCheckState(Qt.CheckState.Unchecked if not selected else Qt.CheckState.Checked if len(selected) == available_count else Qt.CheckState.PartiallyChecked)
        del blocker
        self._refresh_permissions()
        self.selectionChanged.emit(selected)

    def get_selected_sources(self) -> list[str]:
        return [key for key, card in self._source_cards.items() if card.available and card.checkbox.isChecked()]

    def set_selected_sources(self, source_ids: list[str]) -> None:
        selected = set(source_ids)
        for key, card in self._source_cards.items():
            blocker = QSignalBlocker(card.checkbox)
            card.checkbox.setChecked(key in selected and card.available)
            del blocker
            _phase(card, "selected" if card.checkbox.isChecked() else "unavailable" if not card.available else "idle")
        self._selection_changed()

    def set_nodes(self, rows: list[dict[str, Any]]) -> None:
        self._nodes = [dict(row) for row in rows if isinstance(row, dict)]
        self._render_nodes()

    def _render_nodes(self) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._nodes))
        running = bool(self._status.get("running"))
        for index, row in enumerate(self._nodes):
            name = _text(row.get("name", row.get("nodeName")), "未命名节点")
            source = _text(row.get("source", row.get("sourceName")), "—")
            port = row.get("port", row.get("localPort"))
            port_text = f"127.0.0.1:{port}" if isinstance(port, int) and not isinstance(port, bool) else _text(port, "待加载")
            status = _text(row.get("status", row.get("result")), "untested").lower()
            status = {"可用": "reachable", "测试失败": "failed", "超时": "timeout", "未测试": "untested", "未检测": "untested", "已失效": "inactive"}.get(status, status)
            delay = row.get("delayMs", row.get("latencyMs"))
            if status in ("reachable", "available", "ok", "success", "passed"):
                result = "可用" if running else "上次可用"
                if isinstance(delay, (int, float)) and not isinstance(delay, bool):
                    result += f" · {int(delay)} ms"
                color, icon = (GREEN if running else MUTED), "mdi6.circle-small"
            elif status == "timeout":
                result, color, icon = "超时", RED, "mdi6.alert-circle-outline"
            elif status in ("failed", "error", "unreachable"):
                result, color, icon = "测试失败", YELLOW, "mdi6.alert-circle-outline"
            elif status in ("testing", "pending", "working"):
                result, color, icon = "检测中", BLUE, "mdi6.progress-clock"
            elif status in ("inactive", "removed"):
                result, color, icon = "节点已移除", MUTED, "mdi6.cancel"
            else:
                result, color, icon = "未检测", MUTED, "mdi6.circle-outline"
            for column, text in enumerate((name, source, port_text, result)):
                item = QTableWidgetItem(text)
                item.setToolTip(text if column != 3 else text + "\n这是最近一次检测结果；不会自动切换线路。")
                if column == 3:
                    item.setForeground(QColor(color))
                    item.setIcon(_icon(icon, color))
                self.table.setItem(index, column, item)
        self.table.setUpdatesEnabled(True)
        self.node_metric.setText(str(len(self._nodes)))
        self.node_title.setText(f"节点列表（{len(self._nodes)}）" if self._nodes else "节点列表")
        self._filter_rows()
        self._refresh_permissions()

    def _filter_rows(self, *_args) -> None:
        query = self.search_edit.text().strip().casefold()
        visible = 0
        for index in range(self.table.rowCount()):
            matched = not query or any(query in self.table.item(index, column).text().casefold() for column in (0, 1))
            self.table.setRowHidden(index, not matched)
            visible += matched
        self.node_hint.setText(f"显示 {visible} / {len(self._nodes)} 个节点 · 手动检测，失败不自动换线。" if self._nodes else "加载后分配固定端口；不会自动选择或切换节点。")

    def set_busy(self, busy: bool, title: str = "") -> None:
        self._busy = bool(busy)
        if busy:
            self.backend_badge.setText(_text(title, "处理中"))
            _phase(self.backend_badge, "busy")
            self.ready_label.setText("处理中")
            if title:
                self.set_message(title)
        else:
            self.ready_label.setText("准备就绪")
            self._update_backend_labels()
        self._refresh_permissions()

    def set_backend_status(self, status: dict[str, Any]) -> None:
        self._status.update(status)
        if isinstance(status.get("message"), str):
            self.set_message(status["message"], bool(status.get("error")))
        self._update_backend_labels()
        self._render_nodes()

    def _update_backend_labels(self) -> None:
        if self._busy:
            return
        running = bool(self._status.get("running"))
        prepared = bool(self._status.get("prepared"))
        pending = bool(self._status.get("pendingChanges"))
        error = bool(self._status.get("error"))
        self.backend_badge.setText("入口状态异常" if error else "订阅选择待应用" if running and pending else "后台运行中" if running else "配置已就绪" if prepared else "后台未运行")
        _phase(self.backend_badge, "error" if error else "prepared" if running and pending else "running" if running else "prepared" if prepared else "idle")
        hint = "本地入口尚未通过校验；请检查状态或停止后台" if error else "当前后台仍使用原选择；点击“应用选择”更新" if running and pending else "浏览器独立选线 · 电脑 Clash 保持不变" if running else "启动后可检测节点并生成浏览器备份" if prepared else "先刷新订阅，再加载所选节点"
        self.backend_hint.setText(_text(self._status.get("hint"), hint))
        self.start_button.setText("后台需检查" if running and error else "应用订阅选择" if running and pending else "已运行" if running else "启动浏览器后台")
        self.start_button.setIcon(qta.icon("mdi6.alert-circle-outline", color=RED, color_disabled=RED) if running and error else qta.icon("mdi6.check-circle-outline", color=GREEN, color_disabled=GREEN) if running and not pending else _icon("mdi6.layers-outline" if pending else "mdi6.play-circle-outline", WHITE))
        _phase(self.start_button, "error" if running and error else "pending" if running and pending else "running" if running else "ready")

    def set_export_paths(self, bak: str | Path = "", mapping: str | Path = "") -> None:
        self._paths = {"bakPath": str(bak) if bak else "", "mapPath": str(mapping) if mapping else ""}
        for key, edit in (("bakPath", self.bak_edit), ("mapPath", self.map_edit)):
            path = self._paths[key]
            edit.setText(Path(path).name if path else "")
            edit.setToolTip(path)
            edit.setCursorPosition(0)
        self.export_button.setText("重新生成 BAK" if self._paths["bakPath"] else "生成合并 BAK")
        _phase(self.export_button, "complete" if self._paths["bakPath"] else "ready")
        self._refresh_permissions()

    def set_output_dir(self, path: str | Path) -> None:
        self.output_edit.setText(str(path))
        self.output_edit.setCursorPosition(0)
        self.output_edit.setToolTip(str(path))

    def get_output_dir(self) -> str:
        return self.output_edit.text().strip()

    def set_message(self, text: str, error: bool = False) -> None:
        clean = _text(text)
        self.message_label.setText(clean)
        self.message_label.setToolTip(clean)
        self.message_label.setStyleSheet(f"color: {RED if error else MUTED};")
        self.message_icon.setPixmap(_icon("mdi6.alert-circle-outline" if error else "mdi6.information-outline", RED if error else BLUE).pixmap(18, 18))

    def show_settings(self, values: dict[str, Any] | None = None, policy_issues: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        dialog = SettingsDialog(self, values, policy_issues)
        return dialog.result_values() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _refresh_permissions(self) -> None:
        busy = self._busy
        running, prepared = bool(self._status.get("running")), bool(self._status.get("prepared"))
        pending = bool(self._status.get("pendingChanges"))
        selected = bool(self.get_selected_sources())
        can_start = bool(self._status.get("canStart", True))
        self.refresh_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        self.all_checkbox.setEnabled(not busy and any(card.available for card in self._source_cards.values()))
        for card in self._source_cards.values():
            card.checkbox.setEnabled(not busy and card.available)
        self.prepare_button.setText("应用选择" if running and pending else "所选订阅已加载" if running else "加载选中订阅")
        _phase(self.prepare_button, "pending" if running and pending else "idle")
        self.prepare_button.setEnabled(not busy and selected and can_start and (not running or pending))
        self.prepare_button.setToolTip("确认后更新本工具后台，可能短暂中断浏览器连接；电脑 Clash 保持原样。" if running and pending else "所选订阅已在后台运行，无需重复应用。" if running else "加载选中的订阅并启动独立后台，保留固定节点端口。")
        self.start_button.setEnabled(not busy and (not running or pending) and selected and can_start)
        self.start_button.setToolTip("确认后应用新的订阅选择，可能短暂中断浏览器连接；电脑 Clash 保持原样。" if running and pending else "后台已运行，无需重复启动。" if running else "加载选中的订阅并启动独立浏览器后台。")
        self.stop_button.setEnabled(not busy and running)
        self.stop_button.setToolTip("只停止本工具管理的浏览器后台；不退出或重载电脑的 Clash。")
        self.test_button.setEnabled(not busy and bool(self._status.get("canTest", running and bool(self._nodes))))
        self.test_button.setToolTip("启动后台后，逐条检测所加载节点；不会切换电脑或浏览器线路。")
        self.export_button.setEnabled(not busy and bool(self._status.get("canExport", running and bool(self._nodes))) and bool(self.get_output_dir()))
        self.export_button.setToolTip("后台运行后生成对应本地入口的备份，在 ZeroOmega 中手动恢复。")
        self.output_edit.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.bak_copy.setEnabled(not busy and bool(self._paths["bakPath"]))
        self.map_copy.setEnabled(not busy and bool(self._paths["mapPath"]))
        self.open_button.setEnabled(not busy and bool(self.get_output_dir()))
        self.hide_button.setEnabled(not busy)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.closeRequested.emit()

    def _apply_density(self, compact: bool) -> None:
        if compact == self._compact or not hasattr(self, "source_help"):
            return
        self._compact = compact
        self.outer.setContentsMargins(20, 12 if compact else 14, 20, 8 if compact else 10)
        self.outer.setSpacing(8 if compact else 10)
        self.source_help.setVisible(not compact)
        self.source_layout.setContentsMargins(12 if compact else 14, 11 if compact else 14, 12 if compact else 14, 11 if compact else 14)
        self.source_layout.setSpacing(8 if compact else 10)
        self.prepare_button.setMinimumHeight(32 if compact else 36)
        self.output_panel.setFixedHeight(180 if compact else 190)
        self.start_button.setMinimumHeight(40 if compact else 44)
        for button in (self.export_button, self.test_button, self.stop_button, self.hide_button):
            button.setMinimumHeight(34 if compact else 36)
        for card in self._source_cards.values():
            card.set_compact(compact)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_density(event.size().height() < 700)


def configure_application(application: QApplication) -> None:
    application.setApplicationName("多订阅后台助手")
    application.setOrganizationName("CodexLocalTools")
    application.setFont(QFont("Microsoft YaHei UI", 10))
