"""Dark Qt presentation layer. It never reads Clash configuration or calls its API."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import sys
from typing import Any

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QSizeGrip, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QAbstractItemView,
)
import qtawesome as qta


BG = "#172230"
CARD = "#1e2b3b"
FIELD = "#192535"
LINE = "#364961"
WHITE = "#f0f4fc"
MUTED = "#afbdd3"
BLUE = "#78b8ff"
PURPLE = "#a18aff"
GREEN = "#52df97"
RED = "#ff6374"
YELLOW = "#ffcb4a"

_ACTIONS = {
    "PROXY": "浏览器所选节点", "DIRECT": "直连", "REJECT": "拒绝",
    "REJECT-DROP": "静默拒绝", "PASS": "继续后续规则", "COMPATIBLE": "兼容动作",
}


def _text(value: Any, fallback: str = "—") -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return fallback
    return "".join(char for char in str(value) if ord(char) >= 32).strip() or fallback


def _icon(name: str, color: str = BLUE) -> QIcon:
    return qta.icon(name.replace("mdi.", "mdi6.", 1), color=color, color_disabled="#66748c")


def _button(text: str, icon: str | None = None, *, name: str = "", parent: QWidget | None = None) -> QPushButton:
    result = QPushButton(text, parent)
    result.setObjectName(name)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.setMinimumHeight(36)
    if icon:
        result.setIcon(_icon(icon))
        result.setIconSize(QSize(24, 24))
        result.setProperty("iconName", icon.replace("mdi.", "mdi6.", 1))
    return result


class _TitleBar(QFrame):
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        window = self.window()
        if event.button() == Qt.MouseButton.LeftButton and isinstance(window, QMainWindow):
            window.showNormal() if window.isMaximized() else window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _DarkDialog(QDialog):
    def __init__(self, parent: QWidget, title: str, width: int = 620) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("DarkDialog")
        self.setWindowTitle(title)
        self.setWindowIcon(parent.windowIcon())
        self.setMinimumWidth(width)
        self.setModal(True)
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(22, 18, 22, 22)
        self.layout_box.setSpacing(16)
        title_bar = _TitleBar(self)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setObjectName("DialogTitle")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        close = _button("", "mdi.close", name="WindowControl")
        close.setFixedSize(32, 32)
        close.clicked.connect(self.reject)
        title_layout.addWidget(close)
        self.layout_box.addWidget(title_bar)


class _PolicyDialog(_DarkDialog):
    def __init__(self, parent: QWidget, rows: list[dict[str, Any]]) -> None:
        super().__init__(parent, "确认分流动作", 680)
        self.resize(720, 500)
        explanation = QLabel("每个混合组都需要明确选择。“浏览器所选节点”使用你在 ZeroOmega 中选择的线路。")
        explanation.setWordWrap(True)
        explanation.setObjectName("Muted")
        self.layout_box.addWidget(explanation)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        contents = QWidget()
        items = QVBoxLayout(contents)
        items.setContentsMargins(0, 0, 8, 0)
        items.setSpacing(14)
        self.combos: dict[str, QComboBox] = {}
        self.allowed: dict[str, list[str]] = {}
        for row in rows:
            name, choices = row.get("name"), row.get("choices")
            if not isinstance(name, str) or not isinstance(choices, list) or not choices or any(action not in _ACTIONS for action in choices):
                raise ValueError("分流动作信息不完整，请重新探测。")
            item = QFrame()
            line = QHBoxLayout(item)
            line.setContentsMargins(0, 0, 0, 0)
            labels = QVBoxLayout()
            title = QLabel(_text(name))
            title.setWordWrap(True)
            labels.addWidget(title)
            current = QLabel("当前动作：" + _ACTIONS.get(row.get("currentAction"), "未知"))
            current.setObjectName("Muted")
            labels.addWidget(current)
            line.addLayout(labels, 1)
            combo = QComboBox()
            combo.setMinimumWidth(170)
            combo.addItem("请选择", None)
            for action in choices:
                combo.addItem(_ACTIONS[action], action)
            combo.currentIndexChanged.connect(self._selection_changed)
            self.combos[name], self.allowed[name] = combo, choices
            line.addWidget(combo)
            items.addWidget(item)
        items.addStretch()
        scroll.setWidget(contents)
        self.layout_box.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = _button("取消生成")
        cancel.clicked.connect(self.reject)
        self.confirm_button = _button("确认并生成", name="DialogPrimary")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.confirm_button)
        self.layout_box.addLayout(buttons)

    def _selection_changed(self, *_args) -> None:
        self.confirm_button.setEnabled(bool(self.combos) and all(combo.currentData() in self.allowed[name] for name, combo in self.combos.items()))

    def choices(self) -> dict[str, str] | None:
        if not self.confirm_button.isEnabled():
            return None
        return {name: combo.currentData() for name, combo in self.combos.items()}


class _StepAction(QPushButton):
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class _Step(QFrame):
    clicked = Signal()

    def __init__(self, number: int, title: str, hint: str, accent: str) -> None:
        super().__init__()
        self.number, self.accent = number, accent
        self.setObjectName("Step")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName(title)
        self.setMinimumWidth(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.circle = QLabel(str(number))
        self.circle.setObjectName("StepCircle")
        self.circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.circle.setFixedSize(64, 64)
        layout.addWidget(self.circle)
        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(5)
        self.action_button = _StepAction(title) if number in (1, 2) else None
        self.title_label = self.action_button if self.action_button else QLabel(title)
        if self.action_button:
            self.action_button.setObjectName("StepAction")
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.clicked.connect(self.clicked)
        else:
            self.title_label.setObjectName("StepTitle")
            self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("StepHint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setMinimumWidth(0)
        labels.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignLeft)
        labels.addWidget(self.hint_label)
        layout.addLayout(labels, 1)
        self.set_state(hint)

    def set_state(self, hint: str, done: bool = False, *, phase: str = "pending") -> None:
        self.phase = "complete" if done else phase if phase in ("pending", "ready", "working", "failed") else "pending"
        self.hint_label.setText(hint)
        self.setAccessibleDescription(hint)
        self.title_label.setProperty("phase", self.phase)
        self.title_label.setProperty("complete", done)
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)
        self.title_label.update()
        color = GREEN if done else YELLOW if self.phase == "working" else RED if self.phase == "failed" else self.accent
        self.circle.setStyleSheet(f"border: 3px solid {color}; border-radius: 32px; background: #273f68; color: #f2f6ff; font-size: 35px; font-weight: 600;")
        if done:
            self.circle.setText("")
            self.circle.setPixmap(_icon("mdi.check", GREEN).pixmap(38, 38))
            self.circle.setProperty("iconName", "mdi6.check")
        else:
            self.circle.setPixmap(QPixmap())
            self.circle.setText(str(self.number))
            self.circle.setProperty("iconName", "")

    def set_availability(self, available: bool) -> None:
        if self.phase in ("pending", "ready"):
            self.set_state(self.hint_label.text(), phase="ready" if available else "pending")

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)


class Window(QMainWindow):
    detectRequested = Signal()
    generateRequested = Signal()
    applyRequested = Signal()
    rollbackRequested = Signal()
    pathsChanged = Signal()

    def __init__(self, version: str = "1.0.2", resources: Path | str | None = None) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resources = Path(resources or getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        self.version = version
        self._busy = False
        self._compact: bool | None = None
        self._medium = False
        self._apply_completed = False
        self._last_operation: int | None = None
        self._permissions = (False, False, False, False)
        self._messages: deque[str] = deque(maxlen=8)
        self.generated_paths = {"bakPath": "", "mapPath": "", "extensionPath": ""}
        self._status_widgets: list[QWidget] = []
        self._file_descriptions: list[QLabel] = []
        self.setWindowTitle("Clash 节点备份助手 · " + version)
        self.setWindowIcon(QIcon(str(self.resources / "assets" / "app.ico")))
        self.setMinimumSize(1100, 760)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        self.resize(min(1200, available.width() - 24) if available else 1200, min(850, available.height() - 24) if available else 850)
        self._build_ui()
        self._style()
        self._apply_density(self.height() < 850 or self.width() < 1250)
        self.clear_results()
        self._apply_permissions()

    def _build_ui(self) -> None:
        self.surface = QFrame()
        self.surface.setObjectName("AppSurface")
        self.setCentralWidget(self.surface)
        self.outer = QVBoxLayout(self.surface)
        self.outer.setContentsMargins(24, 18, 24, 18)
        self.outer.setSpacing(16)
        self.header = _TitleBar()
        self.header.setObjectName("TopHeader")
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(22)
        self.logo = QLabel()
        self.logo.setObjectName("Logo")
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_source = QPixmap(str(self.resources / "assets" / "app-icon-source.png"))
        if self.logo_source.isNull():
            self.logo_source = QPixmap(str(self.resources / "assets" / "app-icon-64.png"))
        head.addWidget(self.logo)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(4)
        self.title_label = QLabel("Clash 节点备份助手")
        self.title_label.setObjectName("AppTitle")
        self.environment_label = QLabel("Clash Verge · Mihomo · 等待探测")
        self.environment_label.setObjectName("Environment")
        titles.addWidget(self.title_label)
        titles.addWidget(self.environment_label)
        head.addLayout(titles, 1)
        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(5)
        self.minimize_button = _button("", "mdi.window-minimize", name="WindowControl")
        self.maximize_button = _button("", "mdi.window-maximize", name="WindowControl")
        self.close_button = _button("", "mdi.close", name="CloseControl")
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setFixedSize(32, 32)
            bar.addWidget(button)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button.clicked.connect(lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        self.close_button.clicked.connect(self.close)
        controls.addLayout(bar)
        controls.addStretch()
        version = QLabel("v" + self.version)
        version.setObjectName("Version")
        version.setAlignment(Qt.AlignmentFlag.AlignRight)
        controls.addWidget(version)
        head.addLayout(controls)
        self.outer.addWidget(self.header)

        self.steps_frame = QFrame()
        self.steps_frame.setObjectName("StepsFrame")
        steps_layout = QHBoxLayout(self.steps_frame)
        steps_layout.setContentsMargins(24, 14, 24, 14)
        steps_layout.setSpacing(16)
        self.detect_step = _Step(1, "1  探测环境", "检查环境并测试指定节点", "#70d7ff")
        self.generate_step = _Step(2, "2  生成备份", "导出 .bak 和端口映射表", "#83b4ff")
        self.apply_step = _Step(3, "写回 Clash 扩展配置", "在右侧写回 · 生成后校验", "#aa83ff")
        self.apply_step.setToolTip("请在右侧点击“写回 Clash 扩展配置”。")
        for index, step in enumerate((self.detect_step, self.generate_step, self.apply_step)):
            if index:
                connector = QFrame()
                connector.setObjectName("StepConnector")
                connector.setFixedSize(42, 2)
                steps_layout.addWidget(connector)
            steps_layout.addWidget(step, 1)
        self.detect_step.clicked.connect(self.detectRequested)
        self.generate_step.clicked.connect(self.generateRequested)
        self.detect_button = self.detect_step.action_button
        self.generate_button = self.generate_step.action_button
        self.outer.addWidget(self.steps_frame)

        self.body = QFrame()
        body_layout = QHBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)
        self.table_card = QFrame()
        self.table_card.setObjectName("Panel")
        self.table_layout = QVBoxLayout(self.table_card)
        self.table_layout.setContentsMargins(18, 16, 18, 14)
        self.table_layout.setSpacing(6)
        self.node_title = QLabel("节点列表")
        self.node_title.setObjectName("PanelTitle")
        self.table_layout.addWidget(self.node_title)
        self.node_subtitle = QLabel("逐条测试指定节点 · 结果为本次检测快照")
        self.node_subtitle.setObjectName("NodeSubtitle")
        self.table_layout.addWidget(self.node_subtitle)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("节点名", "本机端口", "连通性"))
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setMinimumHeight(120)
        self.table_layout.addWidget(self.table, 1)
        self.table_summary = QLabel("探测后显示全部节点；生成后显示本地端口。")
        self.table_summary.setObjectName("Muted")
        self.table_layout.addWidget(self.table_summary)
        body_layout.addWidget(self.table_card, 3)

        self.right_panel = QFrame()
        self.right_panel.setObjectName("Panel")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(18, 16, 18, 16)
        self.right_layout.setSpacing(5)
        self.output_title = QLabel("输出与写回")
        self.output_title.setObjectName("PanelTitle")
        self.right_layout.addWidget(self.output_title)
        status_box = QFrame()
        status_box.setObjectName("WritebackBox")
        status_layout = QHBoxLayout(status_box)
        status_layout.setContentsMargins(10, 3, 6, 3)
        status_layout.addWidget(QLabel("Clash 回写状态"))
        status_layout.addStretch()
        self.writeback_badge = QLabel("生成后校验")
        self.writeback_badge.setObjectName("WritebackBadge")
        self.writeback_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.writeback_badge)
        self.right_layout.addWidget(status_box)
        self.right_layout.addWidget(self._separator())
        directory_label = QLabel("输出目录")
        directory_label.setObjectName("SideSection")
        self.right_layout.addWidget(directory_label)
        directory = QHBoxLayout()
        directory.setContentsMargins(0, 0, 0, 0)
        directory.setSpacing(8)
        self.paths = {name: QLineEdit() for name in ("output", "data", "clash", "state", "template")}
        for entry in self.paths.values():
            entry.textChanged.connect(self.pathsChanged)
            entry.setMinimumWidth(0)
        directory.addWidget(self.paths["output"], 1)
        self.output_browse_button = _button("选择…")
        self.output_browse_button.clicked.connect(lambda: self._browse("output", directory=True))
        directory.addWidget(self.output_browse_button)
        self.right_layout.addLayout(directory)
        advanced_line = QHBoxLayout()
        advanced_line.setContentsMargins(0, 0, 0, 0)
        self.advanced_button = _button("高级路径（可选）", "mdi.chevron-right", name="AdvancedButton")
        self.advanced_button.setIconSize(QSize(18, 18))
        self.advanced_button.clicked.connect(self._show_advanced)
        advanced_line.addWidget(self.advanced_button)
        advanced_line.addStretch()
        self.right_layout.addLayout(advanced_line)
        self.output_note = QLabel("备份与映射表保存在上面指定的目录。")
        self.output_note.setObjectName("SmallMuted")
        self.output_note.setWordWrap(True)
        self.right_layout.addWidget(self.output_note)
        self.right_layout.addWidget(self._separator())
        files_title = QLabel("生成的文件")
        files_title.setObjectName("SideSection")
        self.right_layout.addWidget(files_title)
        self.bak_field, self.bak_copy_button = self._file_row("BAK 文件", "bakPath")
        self.map_field, self.map_copy_button = self._file_row("端口映射表", "mapPath")
        self.right_layout.addStretch(1)
        self.info_box = QFrame()
        self.info_box.setObjectName("InfoBox")
        info_layout = QHBoxLayout(self.info_box)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_layout.setSpacing(9)
        info_icon = QLabel()
        info_icon.setPixmap(_icon("mdi.information-outline").pixmap(24, 24))
        info_icon.setFixedWidth(24)
        info_layout.addWidget(info_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.info_label = QLabel("写回保存当前订阅扩展，并重载 Mihomo。\n失败节点仍导出；不会自动切换其他线路。")
        self.info_label.setObjectName("InfoText")
        self.info_label.setWordWrap(True)
        info_layout.addWidget(self.info_label, 1)
        self.right_layout.addWidget(self.info_box)
        self.apply_button = _button("写回 Clash 扩展配置", "mdi.tray-arrow-up", name="ApplyButton")
        self.apply_button.setIcon(_icon("mdi.tray-arrow-up", "#ffffff"))
        self.apply_button.clicked.connect(self.applyRequested)
        self.right_layout.addWidget(self.apply_button)
        utilities = QHBoxLayout()
        utilities.setContentsMargins(0, 0, 0, 0)
        utilities.setSpacing(10)
        self.open_button = _button("打开输出目录", "mdi.folder-outline", name="UtilityButton")
        self.rollback_button = _button("回滚本工具", "mdi.backup-restore", name="UtilityButton")
        self.open_button.clicked.connect(self._open_output)
        self.rollback_button.clicked.connect(self.rollbackRequested)
        utilities.addWidget(self.open_button, 1)
        utilities.addWidget(self.rollback_button, 1)
        self.right_layout.addLayout(utilities)
        body_layout.addWidget(self.right_panel, 2)
        self.outer.addWidget(self.body, 1)

        self.footer = QFrame()
        self.footer.setObjectName("Footer")
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(0, 8, 0, 0)
        foot.setSpacing(12)
        footer_icon = QLabel()
        footer_icon.setPixmap(_icon("mdi.information-outline").pixmap(22, 22))
        foot.addWidget(footer_icon)
        self.status_label = QLabel()
        self.status_label.setObjectName("FooterText")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        foot.addWidget(self.status_label, 1)
        self.activity_icon = QLabel()
        self.activity_icon.setFixedSize(14, 14)
        foot.addWidget(self.activity_icon)
        self.activity_label = QLabel("准备就绪")
        self.activity_label.setObjectName("FooterText")
        foot.addWidget(self.activity_label)
        self.size_grip = QSizeGrip(self.footer)
        self.size_grip.setObjectName("ResizeGrip")
        self.size_grip.setFixedSize(16, 16)
        foot.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        self.outer.addWidget(self.footer)
        self._build_advanced()

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setObjectName("Separator")
        line.setFixedHeight(1)
        return line

    def _file_row(self, label: str, key: str) -> tuple[QLineEdit, QPushButton]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        description = QLabel(label)
        description.setObjectName("FileDescription")
        description.setFixedWidth(90)
        self._file_descriptions.append(description)
        row.addWidget(description)
        entry = QLineEdit()
        entry.setReadOnly(True)
        entry.setMinimumWidth(0)
        entry.setPlaceholderText("用于 ZeroOmega 恢复" if key == "bakPath" else "生成后显示端口映射表")
        palette = entry.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8e9fb8"))
        entry.setPalette(palette)
        row.addWidget(entry, 1)
        copy = _button("", "mdi.content-copy", name="CopyButton")
        copy.setFixedWidth(32)
        copy.setToolTip("复制完整文件路径")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.generated_paths[key]))
        row.addWidget(copy)
        self.right_layout.addLayout(row)
        return entry, copy

    def _build_advanced(self) -> None:
        self.advanced_dialog = _DarkDialog(self, "高级路径", 680)
        description = QLabel("留空自动识别；路径改变后需要重新探测。")
        description.setObjectName("Muted")
        self.advanced_dialog.layout_box.addWidget(description)
        self._advanced_browse_buttons: list[QPushButton] = []
        for name, label in (("data", "Clash 数据目录"), ("clash", "Clash 程序目录"), ("state", "本工具私有状态目录"), ("template", "ZeroOmega 原生 .bak 模板（可选）")):
            self.advanced_dialog.layout_box.addWidget(QLabel(label))
            row = QHBoxLayout()
            row.addWidget(self.paths[name], 1)
            browse = _button("选择…")
            browse.clicked.connect(lambda _checked=False, key=name: self._browse(key, directory=key != "template"))
            self._advanced_browse_buttons.append(browse)
            row.addWidget(browse)
            self.advanced_dialog.layout_box.addLayout(row)
        self.advanced_dialog.layout_box.addWidget(QLabel("Clash 持久扩展配置路径"))
        self.extension_field = QLineEdit()
        self.extension_field.setReadOnly(True)
        self.extension_field.setPlaceholderText("生成后显示")
        self.advanced_dialog.layout_box.addWidget(self.extension_field)
        close = _button("完成", name="DialogPrimary")
        close.clicked.connect(self.advanced_dialog.accept)
        self.advanced_dialog.layout_box.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _style(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{ color: {WHITE}; font-family: 'Microsoft YaHei UI'; font-size: 15px; }}
            QMainWindow {{ background: {BG}; }}
            QFrame#AppSurface {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1c293b,stop:1 #15212c); border: 1px solid #41536e; border-radius: 12px; }}
            QFrame#Panel, QFrame#StepsFrame {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #233144,stop:1 #1b2836); border: 1px solid {LINE}; border-radius: 12px; }}
            QFrame#DarkDialog {{ background: {CARD}; border: 1px solid #526988; border-radius: 12px; }}
            QDialog#DarkDialog {{ background: {CARD}; border: 1px solid #526988; border-radius: 12px; }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#AppTitle {{ font-size: 44px; font-weight: 600; }}
            QLabel#Environment {{ color: #c3d4ef; font-size: 18px; }}
            QLabel#Version {{ color: #8fa8c8; font-size: 13px; }}
            QLabel#Logo {{ background: #0d1725; border: 2px solid #36577f; border-radius: 19px; }}
            QLabel#PanelTitle {{ font-size: 23px; font-weight: 600; }}
            QLabel#DialogTitle {{ font-size: 24px; font-weight: 600; }}
            QLabel#StepTitle {{ font-size: 21px; font-weight: 600; }}
            QLabel#StepTitle[phase="complete"] {{ color: #78e4ac; }}
            QLabel#StepTitle[phase="working"] {{ color: #ffcb4a; }}
            QLabel#StepTitle[phase="failed"] {{ color: #ff8090; }}
            QLabel#StepHint {{ font-size: 16px; color: #b7c9e8; }}
            QLabel#Muted, QLabel#NodeSubtitle {{ font-size: 14px; color: {MUTED}; }}
            QLabel#SmallMuted, QLabel#FileDescription {{ font-size: 13px; color: {MUTED}; }}
            QLabel#SideSection {{ font-size: 17px; font-weight: 600; }}
            QLabel#InfoText {{ font-size: 13px; color: #c6d8f6; }}
            QLabel#FooterText {{ font-size: 14px; color: #bdcce4; }}
            QFrame#StepConnector {{ background: #679eff; border: none; }}
            QFrame#Separator {{ background: {LINE}; border: none; }}
            QFrame#Footer {{ border-top: 1px solid {LINE}; background: transparent; }}
            QFrame#WritebackBox {{ background: #1a2839; border: 1px solid #415671; border-radius: 8px; }}
            QLabel#WritebackBadge {{ font-size: 13px; padding: 2px 12px; border: 1px solid #8263c9; border-radius: 13px; color: #b397ff; background: #30305b; }}
            QFrame#InfoBox {{ border: 1px solid #45679b; border-radius: 9px; background: #24364f; }}
            QLineEdit, QComboBox {{ background: {FIELD}; border: 1px solid #5c7394; border-radius: 6px; padding: 5px 9px; color: {WHITE}; font-size: 14px; selection-background-color: #53649b; }}
            QLineEdit:focus, QComboBox:focus {{ border-color: #93a3ff; }}
            QLineEdit:disabled {{ color: #74849c; border-color: #3c4c65; }}
            QComboBox QAbstractItemView {{ background: {CARD}; color: {WHITE}; selection-background-color: #485b86; }}
            QPushButton {{ background: #25374f; border: 1px solid #59749b; border-radius: 7px; padding: 4px 11px; color: {WHITE}; }}
            QPushButton:hover {{ background: #314769; border-color: #8badde; }}
            QPushButton:pressed {{ background: #1f2f49; }}
            QPushButton:disabled {{ color: #70829e; border-color: #3c4c65; background: #233144; }}
            QPushButton#WindowControl, QPushButton#CloseControl {{ border: none; background: transparent; padding: 0; }}
            QPushButton#WindowControl:hover {{ background: #344660; }}
            QPushButton#CloseControl:hover {{ background: #954257; }}
            QPushButton#AdvancedButton {{ color: #93b9f2; font-size: 13px; padding: 2px 8px; }}
            QPushButton#StepAction {{ background: #293f58; border: 1px solid #6189b8; border-radius: 6px; padding: 3px 10px; font-weight: 600; }}
            QPushButton#StepAction:hover {{ background: #375878; border-color: #98d9ff; }}
            QPushButton#StepAction:focus {{ border: 2px solid #8bd8ff; background: #304e70; }}
            QPushButton#StepAction:disabled {{ background: #263448; border-color: #455a76; color: #788ba8; }}
            QPushButton#StepAction[phase="complete"], QPushButton#StepAction[phase="complete"]:disabled {{ background: #234b47; border: 1px solid #5fb892; color: #a0edbd; }}
            QPushButton#StepAction[phase="complete"]:hover {{ background: #2d6052; border-color: #8be4b7; }}
            QPushButton#StepAction[phase="complete"]:focus {{ border: 2px solid #8be4b7; }}
            QPushButton#StepAction[phase="working"], QPushButton#StepAction[phase="working"]:disabled {{ background: #4b3f2d; border: 1px solid #c7a452; color: #ffdb7b; }}
            QPushButton#StepAction[phase="failed"], QPushButton#StepAction[phase="failed"]:disabled {{ background: #4a2e3c; border: 1px solid #c96a80; color: #ff9aaa; }}
            QPushButton#ApplyButton, QPushButton#DialogPrimary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #885cff,stop:0.46 #5a41ec,stop:1 #306ef7); border: 1px solid #adbcff; color: #ffffff; font-size: 20px; font-weight: 600; border-radius: 9px; }}
            QPushButton#ApplyButton:hover, QPushButton#DialogPrimary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #a47aff,stop:1 #4788ff); }}
            QPushButton#ApplyButton:disabled {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #885cff,stop:0.46 #5a41ec,stop:1 #306ef7); border-color: #adbcff; color: #d6dcff; }}
            QPushButton#ApplyButton[complete="true"], QPushButton#ApplyButton[complete="true"]:disabled {{ background: #234b47; border: 1px solid #5fb892; color: #8be4b7; }}
            QPushButton#UtilityButton {{ font-size: 16px; border-radius: 9px; background: #1a293c; padding: 4px 8px; }}
            QPushButton#CopyButton {{ padding: 2px; border-radius: 6px; }}
            QTableWidget {{ background: #172536; alternate-background-color: #1b293b; color: {WHITE}; font-size: 16px; border: 1px solid {LINE}; border-radius: 0; selection-background-color: #334769; outline: 0; }}
            QTableWidget::item {{ color: {WHITE}; border-bottom: 1px solid #304158; padding-left: 11px; }}
            QHeaderView::section {{ background: #293b54; color: {WHITE}; border: none; border-bottom: 1px solid #45576f; border-right: 1px solid #3d4d63; padding-left: 12px; font-size: 16px; }}
            QScrollBar:vertical {{ background: #162333; width: 13px; margin: 2px; border: none; }}
            QScrollBar::handle:vertical {{ background: #53647e; border-radius: 4px; min-height: 35px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; background: transparent; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollArea {{ background: transparent; border: none; }}
            QSizeGrip#ResizeGrip {{ background: transparent; border: none; }}
            QToolTip {{ background: #22344d; color: {WHITE}; border: 1px solid #5a7092; padding: 5px; }}
        """)

    def _apply_density(self, compact: bool) -> None:
        medium = compact and self.height() >= 820 and self.width() >= 1180
        if self._compact == compact and self._medium == medium:
            return
        self._compact = compact
        self._medium = medium
        self.outer.setContentsMargins(18 if compact else 24, 12 if compact else 18, 18 if compact else 24, 12 if compact else 18)
        self.outer.setSpacing(12 if compact else 16)
        self.header.setFixedHeight(80 if compact else 96)
        size = 72 if compact else 88
        self.logo.setFixedSize(size, size)
        self.logo.setPixmap(self.logo_source.scaled(size - 10, size - 10, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.title_label.setStyleSheet(f"font-size: {36 if compact else 44}px; font-weight: 600;")
        self.steps_frame.setFixedHeight(96 if compact else 112)
        for step in (self.detect_step, self.generate_step, self.apply_step):
            step.title_label.setStyleSheet(f"font-size: {18 if medium else 17 if compact else 21}px; font-weight: 600;")
            step.hint_label.setStyleSheet(f"font-size: {14 if medium else 13 if compact else 16}px; color: #b7c9e8;")
            if step.action_button:
                step.action_button.setFixedHeight(32 if compact else 36)
        self.table_layout.setContentsMargins(14 if compact else 18, 12 if compact else 16, 14 if compact else 18, 10 if compact else 14)
        self.right_layout.setContentsMargins(14 if compact else 18, 12 if compact else 16, 14 if compact else 18, 12 if compact else 16)
        self.right_layout.setSpacing(11 if medium else 4 if compact else 10)
        self.output_title.setStyleSheet(f"font-size: {20 if compact else 23}px; font-weight: 600;")
        self.info_label.setStyleSheet(f"font-size: {13 if medium else 12 if compact else 14}px; color: #c6d8f6;")
        self.output_note.setStyleSheet(f"font-size: {13 if compact else 15}px; color: {MUTED};")
        self.node_subtitle.setStyleSheet(f"font-size: {13 if compact else 15}px; color: {MUTED};")
        for description in self._file_descriptions:
            description.setFixedWidth(90)
            description.setStyleSheet(f"font-size: {14 if medium else 13 if compact else 15}px; color: {MUTED};")
        self.output_note.setVisible(not compact or medium)
        self.table.verticalHeader().setDefaultSectionSize(38 if medium else 34 if compact else 42)
        self.table.horizontalHeader().setFixedHeight(36 if compact else 44)
        self.table.horizontalHeader().setStyleSheet(f"QHeaderView::section {{ font-size: {14 if compact else 18}px; }}")
        self.table.setColumnWidth(1, 164 if medium else 154 if compact else 185)
        self.table.setColumnWidth(2, 186 if medium else 170 if compact else 210)
        self.table.setStyleSheet(f"font-size: {16 if medium else 14 if compact else 18}px;")
        for widget in self._status_widgets:
            for label in widget.findChildren(QLabel, "NodeStatus"):
                label.setStyleSheet(f"color: {label.property('statusColor')}; font-size: {16 if medium else 14 if compact else 18}px;")
        for entry in list(self.paths.values()) + [self.bak_field, self.map_field]:
            entry.setFixedHeight(36 if medium else 34 if compact else 38)
            entry.setStyleSheet(f"font-size: {15 if medium else 14 if compact else 16}px;")
        self.output_browse_button.setFixedHeight(36 if medium else 34 if compact else 38)
        self.bak_copy_button.setFixedHeight(36 if medium else 34 if compact else 38)
        self.map_copy_button.setFixedHeight(36 if medium else 34 if compact else 38)
        self.advanced_button.setFixedHeight(26 if compact else 28)
        self.apply_button.setFixedHeight(48 if compact else 56)
        self.apply_button.setStyleSheet(f"font-size: {17 if compact else 20}px; font-weight: 600;")
        self.open_button.setFixedHeight(40 if compact else 46)
        self.rollback_button.setFixedHeight(40 if compact else 46)
        for button in (self.open_button, self.rollback_button):
            button.setStyleSheet(f"font-size: {14 if compact else 16}px;")
        self.footer.setFixedHeight(34 if compact else 40)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "outer"):
            self._apply_density(self.height() < 850 or self.width() < 1250)
            self._fit_file_text()

    def _fit_file_text(self) -> None:
        for field in (self.bak_field, self.map_field):
            size = 15 if self._medium else 14 if self._compact else 16
            font = field.font()
            font.setPixelSize(size)
            while size > 10 and QFontMetrics(font).horizontalAdvance(field.text()) > max(0, field.width() - 20):
                size -= 1
                font.setPixelSize(size)
            field.setStyleSheet(f"font-size: {size}px;")

    def closeEvent(self, event) -> None:
        if self._busy:
            self._message_dialog("任务仍在执行", "请等待当前任务完成后关闭窗口。")
            event.ignore()
            return
        event.accept()

    def path_values(self) -> dict[str, str]:
        return {name: entry.text().strip() for name, entry in self.paths.items()}

    def set_path_values(self, values: dict[str, str]) -> None:
        for name, value in values.items():
            if name in self.paths and isinstance(value, str):
                blocker = QSignalBlocker(self.paths[name])
                self.paths[name].setText(value)
                del blocker

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.apply_button.setText("正在处理…" if busy else "已写回并生效" if self._apply_completed else "写回 Clash 扩展配置")
        self.activity_label.setText("处理中" if busy else "准备就绪")
        self.activity_icon.setPixmap(_icon("mdi.circle", YELLOW if busy else GREEN).pixmap(14, 14))
        self._apply_permissions()

    def set_permissions(self, can_generate: bool, can_apply: bool, installed: bool, output_available: bool) -> None:
        self._permissions = (bool(can_generate), bool(can_apply), bool(installed), bool(output_available))
        self._apply_permissions()

    def _apply_permissions(self) -> None:
        generate, apply, installed, output = self._permissions
        self.apply_button.setToolTip(
            "请等待当前任务完成。" if self._busy else
            "已校验生效，无需重复写回。" if self._apply_completed else
            "点击后确认写回；热重载可能短暂中断连接。" if apply else
            "请先完成探测环境和生成备份；生成后校验是否需要写回。"
        )
        for widget, allowed in ((self.detect_step, True), (self.generate_step, generate), (self.apply_button, apply), (self.rollback_button, installed), (self.open_button, output), (self.advanced_button, True), (self.output_browse_button, True)):
            widget.setEnabled(allowed and not self._busy)
        self.apply_step.setEnabled(True)
        self.detect_step.set_availability(not self._busy)
        self.generate_step.set_availability(generate and not self._busy)
        self.apply_step.set_availability(apply and not self._busy)
        for entry in self.paths.values():
            entry.setEnabled(not self._busy)
        for button in self._advanced_browse_buttons:
            button.setEnabled(not self._busy)
        self.bak_copy_button.setEnabled(bool(self.generated_paths["bakPath"]) and not self._busy)
        self.map_copy_button.setEnabled(bool(self.generated_paths["mapPath"]) and not self._busy)

    def clear_results(self) -> None:
        self.table.setRowCount(0)
        self._status_widgets.clear()
        self.node_title.setText("节点列表")
        self.table_summary.setText("探测后显示全部节点；生成后显示本地端口。")
        self.bak_field.clear()
        self.map_field.clear()
        self.extension_field.clear()
        for field in (self.bak_field, self.map_field, self.extension_field):
            field.setToolTip("")
        self.generated_paths = {"bakPath": "", "mapPath": "", "extensionPath": ""}
        self._set_apply_completed(False)
        self.detect_step.set_state("检查环境并测试指定节点")
        self.generate_step.set_state("导出 .bak 和端口映射表")
        self.apply_step.set_state("在右侧写回 · 生成后校验")
        self._writeback("生成后校验", PURPLE)
        self.environment_label.setText("Clash Verge · Mihomo · 等待探测")
        self.tell("先在 Clash 更新订阅，再点击探测环境。")
        self.activity_label.setText("处理中" if self._busy else "准备就绪")
        self.activity_icon.setPixmap(_icon("mdi.circle", YELLOW if self._busy else GREEN).pixmap(14, 14))
        self._apply_permissions()

    @staticmethod
    def _checks(checks: Any) -> dict[str, dict[str, Any]]:
        rows = checks.get("checks", []) if isinstance(checks, dict) else []
        return {row["nodeName"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("nodeName"), str)} if isinstance(rows, list) else {}

    @staticmethod
    def _status(check: Any, active: bool = True) -> tuple[str, str]:
        if not active:
            return "已失效", "#8997ae"
        if isinstance(check, dict):
            delay = check.get("delayMs")
            if check.get("status") == "reachable" and isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0:
                return "可用 · " + str(delay) + " ms", GREEN
            if check.get("status") == "timeout":
                return "超时", RED
            if check.get("status") == "failed":
                return "测试失败", YELLOW
        return "未测试", "#a5b2c8"

    def _fill_table(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.table.setRowCount(len(rows))
        self._status_widgets.clear()
        for index, (name, port, status, color) in enumerate(rows):
            for column, text in enumerate((name, port)):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(WHITE))
                item.setToolTip(text)
                self.table.setItem(index, column, item)
            item = QTableWidgetItem("")
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, status)
            item.setToolTip(status)
            self.table.setItem(index, 2, item)
            indicator = QWidget()
            indicator.setToolTip(status)
            indicator.setProperty("statusText", status)
            indicator.setAccessibleName(status)
            line = QHBoxLayout(indicator)
            line.setContentsMargins(10, 0, 5, 0)
            line.setSpacing(8)
            dot = QLabel()
            dot.setPixmap(_icon("mdi.circle", color).pixmap(14, 14))
            dot.setFixedSize(14, 14)
            dot.setProperty("iconName", "mdi6.circle")
            line.addWidget(dot)
            label = QLabel(status)
            label.setObjectName("NodeStatus")
            label.setProperty("statusColor", color)
            label.setStyleSheet(f"color: {color}; font-size: {16 if self._medium else 14 if self._compact else 18}px;")
            line.addWidget(label)
            line.addStretch()
            self.table.setCellWidget(index, 2, indicator)
            self._status_widgets.append(indicator)

    def render_detect(self, context: dict[str, Any]) -> None:
        if not context.get("ok"):
            self.clear_results()
            self.detect_step.set_state("探测未通过 · 请处理提示", phase="failed")
            self.tell("探测未通过", "请处理提示后重新探测。")
            return
        self._last_operation = None
        self.environment_label.setText("Clash Verge " + _text(context.get("clashVersion")) + "  ·  Mihomo " + _text(context.get("coreVersion")) + "  ·  " + ("规则模式" if context.get("mode") == "rule" else _text(context.get("mode"))))
        suggested = context.get("outputSuggested")
        if not self.paths["output"].text().strip() and isinstance(suggested, str):
            self.set_path_values({"output": suggested})
        checks = self._checks(context.get("_nodeChecks"))
        rows = [(name, "待生成", *self._status(check)) for name, check in checks.items()]
        self._fill_table(rows)
        total = _text(context.get("nodeCount"), str(len(rows)))
        self.node_title.setText("节点列表（共 " + total + " 个）")
        reachable = sum(self._status(check)[1] == GREEN for check in checks.values())
        self.table_summary.setText("本次测试 " + str(len(rows)) + " 个 · 可用 " + str(reachable) + " 个")
        self.detect_step.set_state("已完成 · 发现 " + total + " 个节点", done=True)
        self.tell("探测成功", "确认输出目录后生成备份；失败节点仍会完整导出。")

    def render_generate(self, result: dict[str, Any], checks: Any) -> None:
        if (not isinstance(result.get("bakPath"), str) or not result["bakPath"]
            or not isinstance(result.get("mapPath"), str) or not result["mapPath"]
            or not isinstance(result.get("entries"), list)
            or not isinstance(result.get("needsApply"), bool)):
            raise ValueError("备份生成结果格式异常，请重新探测。")
        self._last_operation = None
        lookup = self._checks(checks)
        entries = result.get("entries", [])
        rows = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("nodeName") or entry.get("profileName")
            check = lookup.get(name) if isinstance(name, str) else None
            status, color = self._status(check, active=entry.get("active") is True)
            rows.append((_text(name), "127.0.0.1:" + _text(entry.get("port")), status, color))
        self._fill_table(rows)
        for key in self.generated_paths:
            self.generated_paths[key] = _text(result.get(key), "")
        for field, key in ((self.bak_field, "bakPath"), (self.map_field, "mapPath")):
            path = self.generated_paths[key]
            field.setText(Path(path).name if path else "")
            field.setCursorPosition(0)
            field.setToolTip(path)
        self.extension_field.setText(self.generated_paths["extensionPath"])
        self.extension_field.setToolTip(self.generated_paths["extensionPath"])
        self._fit_file_text()
        self.node_title.setText("节点列表（共 " + str(len(rows)) + " 个）")
        self.table_summary.setText("当前 " + _text(result.get("active"), "0") + " 个 · 已失效 " + _text(result.get("inactive"), "0") + " 个 · 端口固定保留")
        self.generate_step.set_state("已完成 · 已生成 .bak 和映射表", done=True)
        if result.get("needsApply"):
            self._set_apply_completed(False)
            self._writeback("待写回", PURPLE)
            self.apply_step.set_state("待写回 · 请在右侧确认", phase="ready")
            self.tell("备份已生成", "点击写回 Clash 扩展配置，再到 ZeroOmega 恢复备份。")
        else:
            self._set_apply_completed(True)
            self._writeback("已写回并生效", GREEN)
            self.apply_step.set_state("已生效 · 无需重复写回", done=True)
            self.tell("备份已生成", "配置已生效，可在 ZeroOmega 中恢复 .bak。")
        self._apply_permissions()

    def _writeback(self, text: str, color: str) -> None:
        self.writeback_badge.setText(text)
        self.writeback_badge.setMinimumHeight(24 if self._compact else 28)
        self.writeback_badge.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: {12 if self._compact else 13}px; padding: 2px 12px; background: #29394e; font-size: {13 if self._compact else 15}px;")

    def _set_apply_completed(self, completed: bool) -> None:
        self._apply_completed = bool(completed)
        self.apply_button.setProperty("complete", self._apply_completed)
        if completed:
            self.apply_button.setIcon(qta.icon("mdi6.check-circle-outline", color=GREEN, color_disabled=GREEN))
            self.apply_button.setProperty("iconName", "mdi6.check-circle-outline")
        else:
            self.apply_button.setIcon(_icon("mdi.tray-arrow-up", "#ffffff"))
            self.apply_button.setProperty("iconName", "mdi6.tray-arrow-up")
        if not self._busy:
            self.apply_button.setText("已写回并生效" if completed else "写回 Clash 扩展配置")
        self.apply_button.style().unpolish(self.apply_button)
        self.apply_button.style().polish(self.apply_button)
        self.apply_button.update()

    def render_apply(self, result: dict[str, Any]) -> None:
        if result.get("status") != "installed-and-live":
            raise ValueError("写回生效状态未通过校验，请重新探测。")
        self._last_operation = None
        self._set_apply_completed(True)
        self._writeback("已写回并生效", GREEN)
        self.apply_step.set_state("已完成 · 已写回并生效", done=True)
        self.tell("写回完成", "请在 ZeroOmega 中从生成的备份文件恢复。")
        warning = _text(result.get("summaryWarning"), "")
        if warning:
            self.tell("写回完成", warning)

    def render_rollback(self) -> None:
        self._last_operation = None
        self.clear_results()
        self.tell("回滚完成", "再次使用前请重新探测；已有备份文件保留。")

    def show_progress(self, done: int, total: int) -> None:
        self._last_operation = 1
        self.detect_step.set_state("正在测试 · " + str(done) + " / " + str(total), phase="working")
        self.tell("正在测试节点 " + str(done) + " / " + str(total), "仅测试指定节点，不切换线路。")

    def tell(self, headline: str, hint: str = "") -> None:
        message = _text(headline, "") + (" · " + _text(hint, "") if hint else "")
        steps = (self.detect_step, self.generate_step, self.apply_step)
        if headline.startswith("正在探测"):
            self._last_operation = 1
            self.detect_step.set_state("正在检查本机环境", phase="working")
        elif headline.startswith("正在生成备份"):
            self._last_operation = 2
            self.generate_step.set_state("正在备份并校验", phase="working")
            self.apply_step.set_state("生成后校验入口状态")
            self._set_apply_completed(False)
        elif headline.startswith(("正在写回", "正在回滚")):
            self._last_operation = 3
            self.apply_step.set_state("正在处理 · 请稍候", phase="working")
        elif headline.startswith("探测未通过"):
            self.detect_step.set_state("探测未通过 · 请处理提示", phase="failed")
        elif headline.startswith("操作未完成") and self._last_operation:
            steps[self._last_operation - 1].set_state("操作未完成 · 请处理提示", phase="failed")
            self._last_operation = None
        elif headline.startswith(("需要重新探测", "路径已变化", "回滚完成")):
            self._last_operation = None
        self.status_label.setText(message)
        self._messages.append(message)
        self.status_label.setToolTip("\n".join(self._messages))

    def _message_dialog(self, title: str, message: str, *, confirm: bool = False) -> bool:
        dialog = _DarkDialog(self, title, 640)
        content = QLabel(message)
        content.setWordWrap(True)
        content.setMinimumWidth(590)
        dialog.layout_box.addWidget(content)
        buttons = QHBoxLayout()
        buttons.addStretch()
        no = _button("取消" if confirm else "知道了")
        no.setDefault(True)
        no.setFocus()
        no.clicked.connect(dialog.reject)
        buttons.addWidget(no)
        if confirm:
            yes = _button("继续", name="DialogPrimary")
            yes.clicked.connect(dialog.accept)
            buttons.addWidget(yes)
        dialog.layout_box.addLayout(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def error_dialog(self, message: str) -> None:
        self._message_dialog("操作未完成", message)

    def confirm_apply(self) -> bool:
        return self._message_dialog("确认写回 Clash 扩展配置", "将先保存私有备份，再把当前订阅的持久扩展脚本写回 Clash，并热重载 Mihomo，使本地 SOCKS5 入口生效。\n\n本工具不会向机场拉取订阅。重载可能短暂中断连接。\n\n是否继续写回？", confirm=True)

    def confirm_rollback(self) -> bool:
        return self._message_dialog("确认回滚本工具", "将按恢复记录移除本工具安装的扩展，并热重载 Mihomo。\n\n可能短暂中断连接；本工具创建的节点端口将不再可用。\n\n是否继续回滚？", confirm=True)

    def review_policies(self, rows: list[dict[str, Any]]) -> dict[str, str] | None:
        dialog = _PolicyDialog(self, rows)
        return dialog.choices() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _show_advanced(self) -> None:
        if not self._busy:
            self.advanced_dialog.exec()

    def _browse(self, name: str, *, directory: bool) -> None:
        if self._busy:
            return
        current = self.paths[name].text().strip()
        if directory:
            selected = QFileDialog.getExistingDirectory(self, "选择目录", current, QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog)
        else:
            selected, _filter = QFileDialog.getOpenFileName(self, "选择 ZeroOmega 原生备份模板", current, "ZeroOmega 备份 (*.bak)", options=QFileDialog.Option.DontUseNativeDialog)
        if selected:
            self.paths[name].setText(selected)

    def _open_output(self) -> None:
        if self._busy:
            return
        directory = self.paths["output"].text().strip()
        if not directory or not Path(directory).is_dir():
            self._message_dialog("目录尚不存在", "请先生成备份，或选择一个已有输出目录。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(directory)):
            self._message_dialog("无法打开目录", "无法打开输出目录，请在资源管理器中查看。")
