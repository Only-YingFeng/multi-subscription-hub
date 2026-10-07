"""Chinese desktop interface for the portable Clash / ZeroOmega helper."""
from __future__ import annotations

from collections import deque
import ctypes
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import portable


_BG = "#121a25"
_CARD = "#1b2635"
_FIELD = "#151f2c"
_LINE = "#35445b"
_TEXT = "#eef3fc"
_MUTED = "#a3b1c7"
_BLUE = "#71caff"
_PURPLE = "#8978ff"


def _text(value: Any, fallback: str = "—") -> str:
    """Display only the public scalar fields of an API result."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return fallback
    return "".join(char for char in str(value) if ord(char) >= 32).strip() or fallback


def _safe_error(error: Exception) -> str:
    try:
        message = portable.safe_error(error)
        if isinstance(message, str) and message:
            return message
    except Exception:
        pass
    return "操作未完成（" + type(error).__name__ + "）。请重新探测。"


_ACTION_LABELS = {
    "PROXY": "浏览器所选节点", "DIRECT": "直连", "REJECT": "拒绝",
    "REJECT-DROP": "静默拒绝", "PASS": "继续后续规则", "COMPATIBLE": "兼容动作",
}


class PolicyReviewDialog:
    """Require an explicit choice for each mixed routing group."""
    def __init__(self, root: tk.Tk, rows: list[dict[str, Any]], on_confirm: Callable[[dict[str, str]], None], on_cancel: Callable[[], None]) -> None:
        self.window = tk.Toplevel(root)
        self.window.title("确认分流动作")
        self.window.transient(root)
        self.window.geometry(f"660x470+{root.winfo_rootx() + 120}+{root.winfo_rooty() + 80}")
        self.window.minsize(570, 390)
        self.window.configure(background=_BG)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self.variables: dict[str, tk.StringVar] = {}
        self.allowed: dict[str, dict[str, str]] = {}
        self.comboboxes: dict[str, ttk.Combobox] = {}

        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="请确认这些组的分流动作", style="Title.TLabel", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="每个组都需要明确选择。“浏览器所选节点”将使用你在 ZeroOmega 中选择的节点。", style="Subtitle.TLabel", wraplength=605, justify="left").grid(row=1, column=0, sticky="ew", pady=(7, 13))
        scroll_host = ttk.Frame(frame, style="Card.TFrame")
        scroll_host.grid(row=2, column=0, sticky="nsew")
        scroll_host.rowconfigure(0, weight=1)
        scroll_host.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(scroll_host, background=_CARD, borderwidth=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(scroll_host, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        contents = ttk.Frame(self.canvas, style="Card.TFrame", padding=12)
        contents.columnconfigure(0, weight=1)
        window_item = self.canvas.create_window((0, 0), window=contents, anchor="nw")
        contents.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window_item, width=event.width))
        self.window.bind("<MouseWheel>", self._scroll, add="+")
        for index, row in enumerate(rows):
            name = row["name"]
            allowed = {_ACTION_LABELS[action]: action for action in row["choices"]}
            self.allowed[name] = allowed
            item = ttk.Frame(contents, style="Card.TFrame")
            item.grid(row=index, column=0, sticky="ew", pady=(0, 12))
            item.columnconfigure(0, weight=1)
            ttk.Label(item, text=_text(name), style="Section.TLabel", wraplength=350, justify="left").grid(row=0, column=0, sticky="w", padx=(0, 12))
            current = _ACTION_LABELS.get(row.get("currentAction"), "未知")
            ttk.Label(item, text="当前动作：" + current, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
            variable = tk.StringVar(self.window, "请选择")
            self.variables[name] = variable
            combo = ttk.Combobox(item, textvariable=variable, values=tuple(allowed), state="readonly", width=17)
            combo.grid(row=0, column=1, rowspan=2, sticky="e")
            self.comboboxes[name] = combo
            variable.trace_add("write", self._selection_changed)
        self.hint = tk.StringVar(self.window, "请为每个组选择一个动作后继续。")
        ttk.Label(frame, textvariable=self.hint, style="Subtitle.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 7))
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="e")
        self.cancel_button = ttk.Button(buttons, text="取消生成", command=self.cancel)
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.confirm_button = ttk.Button(buttons, text="确认并生成", style="Primary.TButton", command=self.confirm, state="disabled")
        self.confirm_button.pack(side="left")
        if root.state() != "withdrawn":
            self.window.grab_set()

    def _selection_changed(self, *_args: Any) -> None:
        ready = all(variable.get() in self.allowed[name] for name, variable in self.variables.items())
        self.confirm_button.configure(state="normal" if ready else "disabled")

    def _scroll(self, event: tk.Event) -> str:
        bounds = self.canvas.bbox("all")
        if bounds and bounds[3] > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def confirm(self) -> None:
        if not all(variable.get() in self.allowed[name] for name, variable in self.variables.items()):
            self.hint.set("请为每个组选择一个动作后继续。")
            return
        choices = {name: self.allowed[name][variable.get()] for name, variable in self.variables.items()}
        self.window.destroy()
        self._on_confirm(choices)

    def cancel(self) -> None:
        self.window.destroy()
        self._on_cancel()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._context: dict[str, Any] | None = None
        self._generation: dict[str, Any] | None = None
        self._policy_dialog: PolicyReviewDialog | None = None
        self._pending_generation: tuple[dict[str, Any], str, str | None] | None = None
        self._busy = False
        self._revision = 0
        self._internal_path_update = False
        self._advanced_visible = False
        self._events: queue.Queue = queue.Queue()
        self._messages: deque[str] = deque(maxlen=8)
        self._path_widgets: list[ttk.Widget] = []
        self._detect_progress_names: set[str] = set()
        self._icon_images: list[tk.PhotoImage] = []

        self.paths = {
            name: tk.StringVar(root) for name in
            ("output", "data", "clash", "state", "template")
        }
        self.stats = {
            name: tk.StringVar(root, "待探测") for name in
            ("clash", "core", "mode", "nodes")
        }
        self.headline = tk.StringVar(root, "等待探测")
        self.status_hint = tk.StringVar(root, "先在 Clash 更新订阅，再点击上方“探测环境”。")
        self.table_hint = tk.StringVar(root, "等待探测节点")
        self.environment = tk.StringVar(root, "Clash Verge · Mihomo · 等待本机探测")
        self.step_hints = {
            "detect": tk.StringVar(root, "检查环境并测试具体节点"),
            "generate": tk.StringVar(root, "导出 .bak 与稳定端口映射"),
            "apply": tk.StringVar(root, "生成后校验"),
        }
        self.summary = tk.StringVar(root, "尚未生成备份")
        self.bak_path = tk.StringVar(root)
        self.map_path = tk.StringVar(root)
        self.extension_path = tk.StringVar(root)
        self.writeback_status = tk.StringVar(root, "生成后校验")

        root.title("Clash 节点备份助手 · " + portable.VERSION)
        root.geometry("1120x800")
        root.minsize(900, 650)
        root.configure(background=_BG)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._configure_styles()
        self._load_icons()
        self._build_ui()
        self.root.after_idle(self._set_native_titlebar)
        for variable in self.paths.values():
            variable.trace_add("write", self._path_changed)
        self._refresh_buttons()
        self._poll_after_id = root.after(100, self._poll_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font)
        style.configure("TFrame", background=_BG)
        style.configure("TLabel", background=_BG, foreground=_TEXT)
        style.configure("Card.TFrame", background=_CARD)
        style.configure("Border.Card.TFrame", background=_CARD, bordercolor=_LINE, borderwidth=1, relief="solid")
        style.configure("Card.TLabel", background=_CARD, foreground=_TEXT)
        style.configure("Muted.TLabel", background=_CARD, foreground=_MUTED, font=(font[0], 9))
        style.configure("Title.TLabel", font=(font[0], 24, "bold"), foreground=_TEXT)
        style.configure("Subtitle.TLabel", font=(font[0], 10), foreground=_MUTED)
        style.configure("Version.TLabel", foreground=_BLUE, font=(font[0], 9))
        style.configure("Section.TLabel", background=_CARD, font=(font[0], 12, "bold"), foreground=_TEXT)
        style.configure("Status.TLabel", background=_CARD, font=(font[0], 11, "bold"), foreground=_BLUE)
        style.configure("Badge.TLabel", background=_CARD, foreground=_PURPLE, font=(font[0], 10, "bold"))
        style.configure("TEntry", fieldbackground=_FIELD, foreground=_TEXT, insertcolor=_TEXT, padding=7, bordercolor=_LINE, lightcolor=_LINE, darkcolor=_LINE)
        style.map("TEntry", fieldbackground=[("readonly", _FIELD), ("disabled", _FIELD)], foreground=[("disabled", "#6e7e95")])
        style.configure("TCombobox", fieldbackground=_FIELD, foreground=_TEXT, background=_LINE, arrowcolor=_TEXT, bordercolor=_LINE, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", _FIELD)], foreground=[("readonly", _TEXT)], selectbackground=[("readonly", _FIELD)], selectforeground=[("readonly", _TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", _FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", _TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#3d4a72")
        style.configure("TButton", padding=(10, 7), background="#28364a", foreground=_TEXT, bordercolor=_LINE, borderwidth=1, relief="flat")
        style.map("TButton", background=[("active", "#35465e"), ("disabled", "#202b3b")], foreground=[("disabled", "#71819a")])
        style.configure("Primary.TButton", background="#324570", foreground=_TEXT, font=(font[0], 11, "bold"), padding=(12, 11), bordercolor="#526ba1")
        style.map("Primary.TButton", background=[("disabled", "#202b3b"), ("active", "#405b93")], foreground=[("disabled", "#71819a")])
        style.configure("Secondary.TButton", background="#6050ca", foreground="#ffffff", font=(font[0], 11, "bold"), padding=(12, 11), bordercolor="#9384ff")
        style.map("Secondary.TButton", background=[("disabled", "#292b45"), ("active", "#7462eb")], foreground=[("disabled", "#7d7b9c")])
        style.configure("Link.TButton", background=_CARD, foreground=_BLUE, padding=(0, 5), borderwidth=0)
        style.map("Link.TButton", background=[("active", _CARD)], foreground=[("disabled", "#71819a")])
        style.configure("Treeview", background=_FIELD, fieldbackground=_FIELD, foreground=_TEXT, rowheight=31, borderwidth=0, bordercolor=_LINE, lightcolor=_LINE, darkcolor=_LINE, relief="flat", font=(font[0], 9))
        style.configure("Treeview.Heading", background="#27354a", foreground="#d8e3f6", padding=9, font=(font[0], 9, "bold"), bordercolor=_LINE, lightcolor=_LINE, darkcolor=_LINE, relief="flat")
        style.map("Treeview", background=[("selected", "#354669")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#32425c")])
        style.configure("TSeparator", background=_LINE)
        style.configure("Vertical.TScrollbar", background="#43536b", troughcolor=_FIELD, arrowcolor=_MUTED, borderwidth=0)
        style.configure("Horizontal.TProgressbar", background=_PURPLE, troughcolor=_FIELD, borderwidth=0)

    def _load_icons(self) -> None:
        resources = getattr(getattr(portable, "m", None), "RESOURCES", Path(__file__).resolve().parent)
        icon = Path(resources) / "assets" / "app.ico"
        if os.name == "nt" and icon.is_file():
            self.root.iconbitmap(default=str(icon))
        for size in (64, 32):
            path = Path(resources) / "assets" / f"app-icon-{size}.png"
            if path.is_file():
                self._icon_images.append(tk.PhotoImage(master=self.root, file=str(path)))
        if self._icon_images:
            self.root.iconphoto(True, *self._icon_images)

    def _set_native_titlebar(self) -> None:
        """Style this application's own window; keep Windows preferences intact."""
        if os.name != "nt":
            return
        try:
            get_root = ctypes.windll.user32.GetAncestor
            get_root.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            get_root.restype = ctypes.c_void_p
            handle = get_root(self.root.winfo_id(), 2)
            set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            set_attribute.restype = ctypes.c_long
            dark = ctypes.c_int(1)
            set_attribute(handle, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            # Documented Windows 11 attributes; older Windows ignores unsupported values.
            # https://learn.microsoft.com/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
            caption = ctypes.c_uint(int(_BG[5:7] + _BG[3:5] + _BG[1:3], 16))
            set_attribute(handle, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        except (AttributeError, OSError):
            pass

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(18, 16))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)
        if self._icon_images:
            ttk.Label(header, image=self._icon_images[0]).grid(row=0, column=0, rowspan=2, padx=(0, 15))
        ttk.Label(header, text="Clash 节点备份助手", style="Title.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, textvariable=self.environment, style="Subtitle.TLabel").grid(row=1, column=1, sticky="w", pady=(3, 0))
        ttk.Label(header, text="v" + portable.VERSION, style="Version.TLabel").grid(row=0, column=2, sticky="ne", padx=(12, 0), pady=(5, 0))

        steps = ttk.Frame(outer, style="Border.Card.TFrame", padding=(12, 12))
        steps.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        for index in range(3):
            steps.columnconfigure(index, weight=1, uniform="steps")
        self.detect_button = ttk.Button(steps, text="1  探测环境", style="Primary.TButton", command=self.detect)
        self.generate_button = ttk.Button(steps, text="2  生成备份", style="Primary.TButton", command=self.generate)
        self.apply_button = ttk.Button(steps, text="3  写回 Clash 扩展配置", style="Secondary.TButton", command=self.apply)
        for index, (name, button) in enumerate((("detect", self.detect_button), ("generate", self.generate_button), ("apply", self.apply_button))):
            pad = (0 if index == 0 else 6, 0 if index == 2 else 6)
            button.grid(row=0, column=index, sticky="ew", padx=pad)
            ttk.Label(steps, textvariable=self.step_hints[name], style="Muted.TLabel").grid(row=1, column=index, sticky="w", padx=pad, pady=(7, 0))

        body = ttk.Frame(outer)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, minsize=450)
        body.columnconfigure(1, weight=2, minsize=330)
        body.rowconfigure(0, weight=1)

        table_card = ttk.Frame(body, style="Border.Card.TFrame", padding=13)
        table_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(2, weight=1)
        title_row = ttk.Frame(table_card, style="Card.TFrame")
        title_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Label(title_row, text="节点列表", style="Section.TLabel").pack(side="left")
        ttk.Label(title_row, textvariable=self.table_hint, style="Muted.TLabel").pack(side="right")
        ttk.Label(table_card, text="逐条测试指定节点 · 结果为本次检测快照", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.table = ttk.Treeview(table_card, columns=("name", "port", "status"), show="headings", selectmode="browse", height=7)
        self.table.heading("name", text="节点名")
        self.table.heading("port", text="本机端口")
        self.table.heading("status", text="连通性")
        self.table.column("name", width=220, minwidth=135, stretch=True)
        self.table.column("port", width=140, minwidth=130, stretch=False)
        self.table.column("status", width=132, minwidth=128, stretch=False)
        self.table.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.table.yview)
        scroll.grid(row=2, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        for tag, foreground in (("inactive", "#78879d"), ("untested", _MUTED), ("reachable", "#75dda9"), ("timeout", "#ff828b"), ("failed", "#f1c76d")):
            self.table.tag_configure(tag, foreground=foreground)
        self.table.tag_configure("even", background="#1a2534")
        self.table.tag_configure("odd", background=_FIELD)
        ttk.Label(table_card, text="选择节点在 ZeroOmega 中完成；此处测试不会切换线路。", style="Muted.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        sidebar_host = ttk.Frame(body, style="Border.Card.TFrame")
        sidebar_host.grid(row=0, column=1, sticky="nsew")
        sidebar_host.columnconfigure(0, weight=1)
        sidebar_host.rowconfigure(0, weight=1)
        self.sidebar_canvas = tk.Canvas(sidebar_host, width=320, background=_CARD, highlightthickness=0, borderwidth=0)
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        sidebar_scroll = ttk.Scrollbar(sidebar_host, orient="vertical", command=self.sidebar_canvas.yview)
        sidebar_scroll.grid(row=0, column=1, sticky="ns")
        self.sidebar_canvas.configure(yscrollcommand=sidebar_scroll.set)
        sidebar = ttk.Frame(self.sidebar_canvas, style="Card.TFrame", padding=14)
        sidebar_window = self.sidebar_canvas.create_window((0, 0), window=sidebar, anchor="nw")
        sidebar.bind("<Configure>", lambda _event: self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all")))
        self.sidebar_canvas.bind("<Configure>", lambda event: self.sidebar_canvas.itemconfigure(sidebar_window, width=event.width))
        self.root.bind("<MouseWheel>", self._scroll_sidebar, add="+")
        sidebar.columnconfigure(0, weight=1)
        ttk.Label(sidebar, text="输出与写回", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        badge = ttk.Frame(sidebar, style="Card.TFrame")
        badge.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        badge.columnconfigure(1, weight=1)
        ttk.Label(badge, text="Clash 写回状态", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.writeback_badge = ttk.Label(badge, textvariable=self.writeback_status, style="Badge.TLabel")
        self.writeback_badge.grid(row=0, column=1, sticky="e")
        ttk.Separator(sidebar).grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._path_row(sidebar, 3, "备份输出目录", "output", directory=True)
        self.advanced_button = ttk.Button(sidebar, text="显示高级路径（可选）", style="Link.TButton", command=self._toggle_advanced)
        self.advanced_button.grid(row=4, column=0, sticky="w", pady=(4, 1))
        self._path_widgets.append(self.advanced_button)
        self.advanced = ttk.Frame(sidebar, style="Card.TFrame")
        self.advanced.columnconfigure(0, weight=1)
        self._path_row(self.advanced, 0, "Clash 数据目录", "data", directory=True)
        self._path_row(self.advanced, 1, "Clash 程序目录", "clash", directory=True)
        self._path_row(self.advanced, 2, "本工具状态目录", "state", directory=True)
        self._path_row(self.advanced, 3, "原生备份模板（可选）", "template", directory=False)
        extension = ttk.Frame(self.advanced, style="Card.TFrame")
        extension.grid(row=4, column=0, sticky="ew", pady=(0, 5))
        extension.columnconfigure(0, weight=1)
        ttk.Label(extension, text="Clash 持久扩展配置路径", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        self.extension_entry = ttk.Entry(extension, textvariable=self.extension_path, state="readonly", width=1)
        self.extension_entry.grid(row=1, column=0, sticky="ew")
        ttk.Label(sidebar, text="路径留空自动识别。订阅请在 Clash 中更新。", style="Muted.TLabel", wraplength=292, justify="left").grid(row=6, column=0, sticky="ew", pady=(4, 8))
        ttk.Separator(sidebar).grid(row=7, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(sidebar, text="生成的文件", style="Section.TLabel").grid(row=8, column=0, sticky="w", pady=(0, 8))
        ttk.Label(sidebar, textvariable=self.summary, style="Muted.TLabel", wraplength=292, justify="left").grid(row=9, column=0, sticky="ew", pady=(0, 8))
        for row, (label, variable) in enumerate((("备份文件 · 用于 ZeroOmega 恢复", self.bak_path), ("节点与端口映射表", self.map_path)), start=10):
            field = ttk.Frame(sidebar, style="Card.TFrame")
            field.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            field.columnconfigure(0, weight=1)
            ttk.Label(field, text=label, style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
            ttk.Entry(field, textvariable=variable, state="readonly", width=1).grid(row=1, column=0, sticky="ew")
        ttk.Label(sidebar, text="生成不会修改 Clash；写回扩展并重载后，在 ZeroOmega 中恢复备份。", style="Muted.TLabel", wraplength=292, justify="left").grid(row=12, column=0, sticky="ew", pady=(2, 0))
        utilities = ttk.Frame(sidebar_host, style="Card.TFrame", padding=(12, 10, 12, 12))
        utilities.grid(row=1, column=0, columnspan=2, sticky="ew")
        utilities.columnconfigure((0, 1), weight=1)
        self.open_button = ttk.Button(utilities, text="打开输出目录", command=self.open_output)
        self.open_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.rollback_button = ttk.Button(utilities, text="回滚本工具", command=self.rollback)
        self.rollback_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        status_card = ttk.Frame(outer, style="Border.Card.TFrame", padding=(12, 9))
        status_card.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        status_card.columnconfigure(0, weight=1)
        ttk.Label(status_card, textvariable=self.headline, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status_card, mode="indeterminate", length=90)
        self.progress.grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Label(status_card, textvariable=self.status_hint, style="Muted.TLabel", wraplength=830, justify="left").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 3))
        self.log = tk.Text(status_card, height=2, wrap="word", relief="flat", borderwidth=0, background=_CARD, foreground="#879bb8", font=("Microsoft YaHei UI", 9), state="disabled", cursor="arrow")
        self.log.grid(row=2, column=0, columnspan=2, sticky="ew")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, name: str, *, directory: bool) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 5))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label, style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))
        entry = ttk.Entry(frame, textvariable=self.paths[name], width=1)
        entry.grid(row=1, column=0, sticky="ew", padx=(0, 5))
        button = ttk.Button(frame, text="选择", command=lambda: self._browse(name, directory))
        button.grid(row=1, column=1)
        self._path_widgets.extend((entry, button))

    def _browse(self, name: str, directory: bool) -> None:
        if self._busy:
            return
        current = self.paths[name].get().strip()
        options: dict[str, Any] = {"parent": self.root}
        if current and Path(current).is_dir():
            options["initialdir"] = current
        if directory:
            chosen = filedialog.askdirectory(title="选择目录", mustexist=name != "output", **options)
        else:
            chosen = filedialog.askopenfilename(title="选择 ZeroOmega 原生备份模板", filetypes=[("ZeroOmega 备份", "*.bak"), ("所有文件", "*.*")], **options)
        if chosen:
            self.paths[name].set(chosen)

    def _toggle_advanced(self) -> None:
        if self._busy:
            return
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced.grid(row=5, column=0, sticky="ew", pady=(3, 0))
            self.advanced_button.configure(text="收起高级路径")
        else:
            self.advanced.grid_remove()
            self.advanced_button.configure(text="显示高级路径（可选）")

    def _scroll_sidebar(self, event: tk.Event) -> str | None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self.sidebar_canvas:
                bounds = self.sidebar_canvas.bbox("all")
                if bounds and bounds[3] > self.sidebar_canvas.winfo_height():
                    self.sidebar_canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            widget = widget.master
        return None

    def _clear_context(self) -> None:
        if self._policy_dialog:
            self._policy_dialog.cancel()
        self._pending_generation = None
        self._revision += 1
        self._context = None
        self._generation = None
        for variable in self.stats.values():
            variable.set("待探测")
        self.environment.set("Clash Verge · Mihomo · 等待本机探测")
        self.step_hints["detect"].set("检查环境并测试具体节点")
        self._detect_progress_names.clear()
        self._clear_generation()

    def _clear_generation(self) -> None:
        self._generation = None
        self.summary.set("尚未生成备份")
        self.bak_path.set("")
        self.map_path.set("")
        self.extension_path.set("")
        self.step_hints["generate"].set("导出 .bak 与稳定端口映射")
        self._set_writeback_status("生成后校验")
        self.table_hint.set("等待探测节点")
        children = self.table.get_children()
        if children:
            self.table.delete(*children)

    def _set_writeback_status(self, status: str) -> None:
        self.writeback_status.set(status)
        self.step_hints["apply"].set(status)
        color = "#75dda9" if status == "已写回并生效" else _PURPLE if status == "待写回" else _MUTED
        self.writeback_badge.configure(foreground=color)

    @staticmethod
    def _node_status(check: Any) -> tuple[str, str]:
        if not isinstance(check, dict):
            return "未测试", "untested"
        status = check.get("status")
        delay = check.get("delayMs")
        if status == "reachable" and isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0:
            return "可用 · " + str(delay) + " ms", "reachable"
        if status == "timeout":
            return "超时", "timeout"
        if status == "failed":
            return "测试失败", "failed"
        return "未测试", "untested"

    def _node_lookup(self) -> dict[str, dict[str, Any]]:
        checks = self._context.get("_nodeChecks", {}) if self._context else {}
        rows = checks.get("checks", []) if isinstance(checks, dict) else []
        return {row["nodeName"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("nodeName"), str)} if isinstance(rows, list) else {}

    def _display_detected_nodes(self) -> None:
        children = self.table.get_children()
        if children:
            self.table.delete(*children)
        checks = self._node_lookup()
        for index, (name, check) in enumerate(checks.items()):
            status, tag = self._node_status(check)
            self.table.insert("", "end", iid=str(index), values=(_text(name), "待生成", status), tags=("even" if index % 2 == 0 else "odd", tag))
        reachable = sum(self._node_status(check)[1] == "reachable" for check in checks.values())
        self.table_hint.set(str(len(checks)) + " 个节点 · 可用 " + str(reachable))

    def _path_changed(self, *_args: Any) -> None:
        if self._internal_path_update:
            return
        self._clear_context()
        self.headline.set("路径已更新")
        self.status_hint.set("请重新探测环境，确认当前路径后再生成备份。")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        valid = bool(self._context and self._context.get("ok"))
        can_generate = valid and bool(self._context.get("canGenerate"))
        can_apply = valid and bool(self._context.get("canApply")) and bool(self._generation and self._generation.get("needsApply"))
        installed = valid and bool(self._context.get("hasInstallation"))
        available = bool(self.paths["output"].get().strip())
        for button, enabled in ((self.detect_button, True), (self.generate_button, can_generate), (self.apply_button, can_apply), (self.rollback_button, installed), (self.open_button, available)):
            button.configure(state="normal" if enabled and not self._busy else "disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in self._path_widgets:
            widget.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        self._refresh_buttons()

    def _append_log(self, message: str) -> None:
        self._messages.append(message)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(self._messages))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _dispatch(self, action: str, operation: Callable[[], dict[str, Any]]) -> None:
        if self._busy:
            return
        revision = self._revision
        self._set_busy(True)

        def work() -> None:
            try:
                result = operation()
                self._events.put((action, revision, result, None))
            except Exception as error:
                # Never expose exception parameters, a traceback, or context.
                self._events.put((action, revision, None, _safe_error(error)))

        threading.Thread(target=work, name="backup-task", daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                action, revision, result, error = self._events.get_nowait()
                if action == "node_progress":
                    if revision == self._revision and self._busy and isinstance(result, dict):
                        self._finish_node_progress(result)
                    continue
                self._set_busy(False)
                if revision != self._revision:
                    self.headline.set("需要重新探测")
                    self.status_hint.set("路径已变化，已清除旧操作结果。")
                    continue
                if error:
                    self._operation_failed(error)
                    continue
                if not isinstance(result, dict):
                    self._operation_failed("返回结果格式异常，请重新探测。")
                    continue
                try:
                    getattr(self, "_finish_" + action)(result)
                except Exception as failure:
                    self._operation_failed(_safe_error(failure))
                self._refresh_buttons()
        except queue.Empty:
            pass
        self._poll_after_id = self.root.after(100, self._poll_events)

    def _operation_failed(self, message: str) -> None:
        self._clear_context()
        self.headline.set("操作未完成")
        self.status_hint.set("请查看下方原因，处理后重新探测。")
        self._append_log(message)
        self._refresh_buttons()
        messagebox.showerror("操作未完成", message, parent=self.root)

    def detect(self) -> None:
        if self._busy:
            return
        selected = {name: self.paths[name].get().strip() or None for name in ("data", "clash", "state")}
        self._clear_context()
        self.headline.set("正在探测本机环境…")
        self.status_hint.set("正在检查 Clash 版本、本机配置，随后测试每条具体节点。")
        self.step_hints["detect"].set("正在检查本机环境")
        revision = self._revision
        def probe() -> dict[str, Any]:
            result = portable.detect(data_directory=selected["data"], clash_directory=selected["clash"], state_directory=selected["state"])
            if result.get("ok"):
                def progress(check: dict[str, Any]) -> None:
                    self._events.put(("node_progress", revision, {"check": check, "total": result.get("nodeCount", 0)}, None))
                result["_nodeChecks"] = portable.check_nodes(result, on_progress=progress)
            return result
        self._dispatch("detect", probe)

    def _finish_node_progress(self, result: dict[str, Any]) -> None:
        check = result.get("check")
        if isinstance(check, dict) and isinstance(check.get("nodeName"), str):
            self._detect_progress_names.add(check["nodeName"])
        done = len(self._detect_progress_names)
        total = _text(result.get("total"), "0")
        self.headline.set("正在测试节点 " + str(done) + " / " + total + "…")
        self.step_hints["detect"].set("连通性检测 · " + str(done) + " / " + total)
        self.status_hint.set("正在逐条测试指定节点；结果是本次检测快照。")

    def _finish_detect(self, result: dict[str, Any]) -> None:
        issues = result.get("issues", [])
        issues = [issue for issue in issues if isinstance(issue, str)] if isinstance(issues, list) else []
        if not result.get("ok"):
            self._clear_context()
            self.headline.set("探测未通过")
            self.status_hint.set("请处理下方提示，必要时在高级路径中选择目录后重试。")
            self._append_log("\n".join(issues) or "未找到支持的本机环境，请确认 Clash 已运行。")
            if not self._advanced_visible:
                self._toggle_advanced()
            return
        self._context = result
        self.stats["clash"].set(_text(result.get("clashVersion")))
        self.stats["core"].set(_text(result.get("coreVersion")))
        modes = {"rule": "规则模式", "global": "全局模式", "direct": "直连模式", "规则": "规则模式"}
        self.stats["mode"].set(modes.get(str(result.get("mode", "")).lower(), "未知模式"))
        self.stats["nodes"].set(_text(result.get("nodeCount"), "0") + " / " + _text(result.get("groupCount"), "0"))
        self.environment.set("Clash Verge " + self.stats["clash"].get() + "  ·  Mihomo " + self.stats["core"].get() + "  ·  " + self.stats["mode"].get())
        suggested = result.get("outputSuggested")
        if not self.paths["output"].get().strip() and isinstance(suggested, str) and suggested:
            self._internal_path_update = True
            try:
                self.paths["output"].set(suggested)
            finally:
                self._internal_path_update = False
        self.headline.set("探测成功")
        self.step_hints["detect"].set("已完成 · " + _text(result.get("nodeCount"), "0") + " 个节点")
        self._display_detected_nodes()
        self.status_hint.set("确认输出目录后，点击“生成备份”。" if result.get("canGenerate") else "当前环境暂不支持生成，请查看下方提示。")
        self._append_log("环境与节点测试完成。测试失败的节点仍会包含在备份中。" + ("已检测到本工具安装，可按需回滚。" if result.get("hasInstallation") else ""))
        if issues:
            self._append_log("\n".join(issues))

    def generate(self) -> None:
        if self._busy or not self._context or not self._context.get("ok") or not self._context.get("canGenerate"):
            return
        output = self.paths["output"].get().strip()
        if not output:
            messagebox.showinfo("请选择输出目录", "请先选择备份输出目录。", parent=self.root)
            return
        template = self.paths["template"].get().strip() or None
        context = self._context
        if self._policy_dialog:
            self._policy_dialog.window.lift()
            return
        if context.get("needsPolicyReview"):
            rows = context.get("groupPolicies")
            if not isinstance(rows, list) or not rows or any(
                not isinstance(row, dict) or not isinstance(row.get("name"), str)
                or not isinstance(row.get("choices"), list) or not row["choices"]
                or any(action not in _ACTION_LABELS for action in row["choices"])
                for row in rows
            ):
                self._operation_failed("分流动作信息不完整，请重新探测。")
                return
            self._pending_generation = (context, output, template)
            self._policy_dialog = PolicyReviewDialog(self.root, rows, self._policy_confirmed, self._policy_cancelled)
            self.headline.set("需要确认分流动作")
            self.status_hint.set("请为弹窗中的每个组明确选择一个动作。取消将不会生成备份。")
            return
        self._start_generation(context, output, template)

    def _policy_cancelled(self) -> None:
        self._policy_dialog = None
        self._pending_generation = None
        self.headline.set("已取消生成")
        self.status_hint.set("分流动作尚未确认；需要时可再次点击“生成备份”。")

    def _policy_confirmed(self, choices: dict[str, str]) -> None:
        self._policy_dialog = None
        if not self._pending_generation:
            return
        context = self._pending_generation[0]
        self.headline.set("正在确认分流动作…")
        self.status_hint.set("正在保存本次选择，随后生成备份。")
        def review() -> dict[str, Any]:
            portable.review_policies(context, choices)
            return {}
        self._dispatch("review", review)

    def _finish_review(self, _result: dict[str, Any]) -> None:
        if not self._pending_generation:
            return
        context, output, template = self._pending_generation
        self._pending_generation = None
        context["needsPolicyReview"] = False
        self._start_generation(context, output, template)

    def _start_generation(self, context: dict[str, Any], output: str, template: str | None) -> None:
        self._clear_generation()
        self.headline.set("正在生成备份…")
        self.step_hints["generate"].set("正在生成 .bak 与端口映射")
        self.status_hint.set("正在生成备份文件与端口映射；此步骤不会修改 Clash。")
        self._dispatch("generate", lambda: portable.generate(context, output_directory=output, template_path=template))

    def _finish_generate(self, result: dict[str, Any]) -> None:
        self._generation = result
        children = self.table.get_children()
        if children:
            self.table.delete(*children)
        self.bak_path.set(_text(result.get("bakPath"), ""))
        self.map_path.set(_text(result.get("mapPath"), ""))
        self.extension_path.set(_text(result.get("extensionPath"), ""))
        self.summary.set("生成完成 · 新增 " + _text(result.get("new"), "0") + " · 当前 " + _text(result.get("active"), "0") + " · 失效 " + _text(result.get("inactive"), "0"))
        entries = result.get("entries", [])
        checks = self._node_lookup()
        for index, entry in enumerate(entries if isinstance(entries, list) else []):
            if not isinstance(entry, dict):
                continue
            active = entry.get("active") is True
            name = entry.get("nodeName") or entry.get("profileName")
            check = checks.get(name) if isinstance(name, str) else None
            status, tag = self._node_status(check) if active else ("已失效", "inactive")
            self.table.insert("", "end", iid=str(index), values=(_text(name), "127.0.0.1:" + _text(entry.get("port")), status), tags=("even" if index % 2 == 0 else "odd", tag))
        self.table_hint.set("当前 " + _text(result.get("active"), "0") + " · 失效 " + _text(result.get("inactive"), "0"))
        self.headline.set("备份已生成")
        self.step_hints["generate"].set("已完成 · .bak 与映射表")
        if result.get("needsApply"):
            self._set_writeback_status("待写回")
            if self._context and self._context.get("canApply"):
                self.status_hint.set("点击“写回 Clash 扩展配置”使本地端口生效，再到 ZeroOmega 恢复备份。")
            else:
                self.status_hint.set("备份已生成。当前环境暂不允许写回，请处理探测提示后重新探测。")
        else:
            self._set_writeback_status("已写回并生效")
            self.status_hint.set("配置已生效，无需重复写回。可在 ZeroOmega 中恢复备份。")
        self._append_log("备份文件与端口映射已生成。生成步骤未修改 Clash。")

    def apply(self) -> None:
        if self._busy or not self._context or not self._context.get("ok") or not self._context.get("canApply") or not self._generation or not self._generation.get("needsApply"):
            return
        if not messagebox.askyesno("确认写回 Clash 扩展配置", "将先保存私有备份，再把当前订阅的扩展脚本写回 Clash，并热重载 Mihomo，使 127.0.0.1 本地 SOCKS5 端口生效。\n\n本工具不会向机场拉取订阅。重载可能短暂中断连接。\n\n是否继续写回？", parent=self.root, default="no"):
            return
        context = self._context
        self.headline.set("正在写回 Clash 扩展配置…")
        self._set_writeback_status("正在写回…")
        self.status_hint.set("正在更新并检查生效状态，请稍候。")
        self._dispatch("apply", lambda: portable.apply(context))

    def _finish_apply(self, result: dict[str, Any]) -> None:
        if result.get("status") != "installed-and-live":
            raise portable.PublicError("写回生效状态未通过校验，请重新探测。")
        if self._context:
            self._context["hasInstallation"] = True
        if self._generation:
            self._generation["needsApply"] = False
        if result.get("extensionPath"):
            self.extension_path.set(_text(result["extensionPath"], ""))
        self._set_writeback_status("已写回并生效")
        self.headline.set("写回完成")
        self.status_hint.set("配置已写回并生效。请在 ZeroOmega 中恢复生成的备份。")
        self._append_log("持久扩展已写回，本地端口已校验。需要恢复时可点击“回滚本工具”。")
        warning = _text(result.get("summaryWarning"), "")
        if warning:
            self._append_log(warning)

    def rollback(self) -> None:
        if self._busy or not self._context or not self._context.get("ok") or not self._context.get("hasInstallation"):
            return
        if not messagebox.askyesno("确认回滚本工具", "将按恢复记录移除本工具安装的配置，并重新加载 Clash。\n\n过程中可能短暂断网；本工具创建的节点端口将不再可用。\n\n是否继续回滚？", parent=self.root, default="no"):
            return
        context = self._context
        self.headline.set("正在回滚本工具…")
        self.status_hint.set("正在恢复并检查本机配置，请稍候。")
        self._dispatch("rollback", lambda: portable.rollback(context))

    def _finish_rollback(self, _result: dict[str, Any]) -> None:
        self._clear_context()
        self.headline.set("回滚完成")
        self.status_hint.set("本工具安装的配置已回滚。再次使用前请重新探测。")
        self._append_log("回滚与本机恢复检查已完成。已有备份文件仍保留在输出目录。")

    def open_output(self) -> None:
        if self._busy:
            return
        directory = self.paths["output"].get().strip()
        if not directory or not Path(directory).is_dir():
            messagebox.showinfo("目录尚不存在", "请先生成备份，或选择一个已有输出目录。", parent=self.root)
            return
        try:
            os.startfile(directory)
        except Exception as error:
            messagebox.showerror("无法打开目录", _safe_error(error), parent=self.root)

    def _close(self) -> None:
        if self._busy:
            messagebox.showinfo("任务仍在执行", "请等待当前任务完成后关闭窗口。", parent=self.root)
            return
        self.root.after_cancel(self._poll_after_id)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
