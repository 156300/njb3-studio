#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_theme.py — NJB3 Studio 视觉设计系统（Design Tokens / Themes）

设计目标（UI Designer 规范）：
  · 简约、大方：低饱和中性底 + 单一青绿强调色，避免视觉噪音。
  · 条框清晰：1px 边框、明确的层级（工作区 < 面板 < 标题栏）。
  · 易读性：正文对比度 >= 7:1（WCAG AAA），次要文字 >= 4.5:1（AA）。
  · 焦点可见：焦点行用灰蓝、失焦行用中性灰、舞台行用暖黄，三态可辨。
  · 系统联动：可跟随 Windows 浅色/深色应用模式自动切换。

本模块自包含，不依赖原 scripts/njb3_skin.py，但对外提供兼容接口。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------- 主题定义
# 每套皮肤是一组语义 token；控件只引用语义名，不写死颜色。
THEMES = {
    "深空石墨": {
        "mode": "dark",
        "bg": "#1b1e22",          # 工作区背景（近黑不纯黑）
        "chrome": "#202429",      # 顶栏 / 状态栏
        "panel": "#262b31",       # 面板内容背景
        "header": "#2e343b",      # 面板标题栏
        "header_fg": "#c7d0da",
        "border": "#3a424b",      # 1px 边框 / 分隔线
        "text": "#e8edf3",        # 正文 AAA
        "text_dim": "#9aa6b2",    # 次要 AA
        "accent": "#31c9a6",      # 青绿强调
        "accent_fg": "#0b201b",
        "select_bg": "#2c6285",   # 焦点行：灰蓝
        "select_fg": "#ffffff",
        "idle_bg": "#343b43",     # 失焦行：中性灰
        "idle_fg": "#c2ccd6",
        "warm_bg": "#54462a",     # 舞台行：暖黄（深底适配）
        "warm_fg": "#ffe6a3",
        "input_bg": "#1e2227",
        "ok": "#4fc98a",
        "warn": "#e6b34a",
        "error": "#ec6a5e",
        "sash": "#3a424b",
        "tab_bg": "#282e35",
        "tab_active": "#343c45",
        "ghost": "#2c6285",
    },
    "午夜蓝": {
        "mode": "dark",
        "bg": "#141b2b",
        "chrome": "#182137",
        "panel": "#1d2740",
        "header": "#25314d",
        "header_fg": "#c3d0e6",
        "border": "#35425f",
        "text": "#e8eefb",
        "text_dim": "#9aabc8",
        "accent": "#4db8ff",
        "accent_fg": "#0a1b2b",
        "select_bg": "#2f5d92",
        "select_fg": "#ffffff",
        "idle_bg": "#283452",
        "idle_fg": "#c2cee3",
        "warm_bg": "#4d4530",
        "warm_fg": "#ffe6a3",
        "input_bg": "#161f33",
        "ok": "#4fc98a",
        "warn": "#e6b34a",
        "error": "#ec6a5e",
        "sash": "#35425f",
        "tab_bg": "#1f2943",
        "tab_active": "#2b3856",
        "ghost": "#2f5d92",
    },
    "云白": {
        "mode": "light",
        "bg": "#eef1f5",
        "chrome": "#f7f9fb",
        "panel": "#ffffff",
        "header": "#e9edf2",
        "header_fg": "#3a4652",
        "border": "#cbd3dc",
        "text": "#1b232b",
        "text_dim": "#56636f",
        "accent": "#0a6e5b",
        "accent_fg": "#ffffff",
        "select_bg": "#c6dcec",
        "select_fg": "#102633",
        "idle_bg": "#e7ebef",
        "idle_fg": "#39444e",
        "warm_bg": "#fff1b8",
        "warm_fg": "#5a4400",
        "input_bg": "#ffffff",
        "ok": "#167a4a",
        "warn": "#9a6a10",
        "error": "#c0392b",
        "sash": "#cbd3dc",
        "tab_bg": "#e6ebf0",
        "tab_active": "#ffffff",
        "ghost": "#8fb4d0",
    },
    "暖米": {
        "mode": "light",
        "bg": "#f2efe9",
        "chrome": "#faf7f1",
        "panel": "#fffdf9",
        "header": "#efe9dd",
        "header_fg": "#4a4438",
        "border": "#d8d0c2",
        "text": "#2b2620",
        "text_dim": "#6b6153",
        "accent": "#a8662f",
        "accent_fg": "#ffffff",
        "select_bg": "#d9e4d0",
        "select_fg": "#25331d",
        "idle_bg": "#ece7dd",
        "idle_fg": "#4a4438",
        "warm_bg": "#ffe9a8",
        "warm_fg": "#5a4400",
        "input_bg": "#fffdf9",
        "ok": "#3f7a46",
        "warn": "#96681a",
        "error": "#b0463a",
        "sash": "#d8d0c2",
        "tab_bg": "#ebe5da",
        "tab_active": "#fffdf9",
        "ghost": "#b9c9a8",
    },
}

DEFAULT_THEME = "深空石墨"
FOLLOW_SYSTEM = "跟随系统"
THEME_ORDER = [FOLLOW_SYSTEM] + list(THEMES.keys())

# 系统模式 -> 采用的主题
SYSTEM_DARK_THEME = "深空石墨"
SYSTEM_LIGHT_THEME = "云白"


def theme_names():
    return list(THEME_ORDER)


def resolve(name):
    """把主题名（含“跟随系统”）解析成具体 token dict。"""
    if name == FOLLOW_SYSTEM:
        name = SYSTEM_LIGHT_THEME if system_is_light() else SYSTEM_DARK_THEME
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def system_is_light():
    """读取 Windows 应用模式：True=浅色，False=深色；非 Windows 默认深色。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return bool(value)
    except Exception:
        return False


def focus_tags(pal):
    """供 Treeview 行 tag 使用的三态焦点配色（与 v24 焦点逻辑兼容）。"""
    return {
        "hot": (pal["select_bg"], pal["select_fg"]),
        "cold": (pal["idle_bg"], pal["idle_fg"]),
        "warm": (pal["warm_bg"], pal["warm_fg"]),
    }


# ---------------------------------------------------------------- WCAG QA
def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(fg, bg):
    """WCAG 对比度（1:1 ~ 21:1）。"""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def audit(name=None):
    """设计 QA：返回该主题关键文字配色的对比度报告。"""
    names = [name] if name else list(THEMES.keys())
    report = {}
    for n in names:
        p = THEMES[n]
        report[n] = {
            "text/panel": round(contrast_ratio(p["text"], p["panel"]), 2),
            "text_dim/panel": round(contrast_ratio(p["text_dim"], p["panel"]), 2),
            "header_fg/header": round(contrast_ratio(p["header_fg"], p["header"]), 2),
            "select_fg/select_bg": round(contrast_ratio(p["select_fg"], p["select_bg"]), 2),
            "accent_fg/accent": round(contrast_ratio(p["accent_fg"], p["accent"]), 2),
        }
    return report


# ---------------------------------------------------------------- 样式应用
def apply_theme(root, name, extra_styles=True):
    """把主题应用到 ttk.Style 与根窗口。返回实际使用的 palette。"""
    pal = resolve(name)
    st = ttk.Style()
    try:
        st.theme_use("clam")
    except Exception:
        pass

    st.configure(".", background=pal["bg"], foreground=pal["text"],
                 fieldbackground=pal["input_bg"],
                 font=("Microsoft YaHei UI", 9),
                 borderwidth=0)
    st.configure("TFrame", background=pal["bg"])
    st.configure("TLabel", background=pal["bg"], foreground=pal["text"])
    st.configure("TLabelframe", background=pal["panel"], bordercolor=pal["border"],
                 relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=pal["panel"], foreground=pal["header_fg"],
                 font=("Microsoft YaHei UI", 9, "bold"))

    st.configure("TButton", background=pal["header"], foreground=pal["text"],
                 bordercolor=pal["border"], relief="raised", padding=(8, 3))
    st.map("TButton",
           background=[("active", pal["tab_active"]), ("disabled", pal["panel"])],
           foreground=[("disabled", pal["text_dim"])])

    st.configure("Accent.TButton", background=pal["accent"], foreground=pal["accent_fg"],
                 bordercolor=pal["accent"], relief="flat", padding=(10, 4),
                 font=("Microsoft YaHei UI", 9, "bold"))
    st.map("Accent.TButton",
           background=[("active", pal["accent"]), ("disabled", pal["border"])],
           foreground=[("disabled", pal["text_dim"])])

    st.configure("Tool.TButton", background=pal["header"], foreground=pal["text"],
                 bordercolor=pal["border"], relief="flat", padding=(4, 2))
    st.map("Tool.TButton", background=[("active", pal["tab_active"])])

    st.configure("TEntry", fieldbackground=pal["input_bg"], foreground=pal["text"],
                 bordercolor=pal["border"], relief="solid", borderwidth=1, padding=3)
    st.configure("TCombobox", fieldbackground=pal["input_bg"], foreground=pal["text"],
                 bordercolor=pal["border"], relief="solid", borderwidth=1)
    st.map("TCombobox",
           fieldbackground=[("readonly", pal["input_bg"]), ("disabled", pal["input_bg"])],
           foreground=[("readonly", pal["text"]), ("disabled", pal["text_dim"])])
    st.configure("TCheckbutton", background=pal["panel"], foreground=pal["text"])
    st.map("TCheckbutton", background=[("active", pal["panel"])])
    st.configure("TRadiobutton", background=pal["panel"], foreground=pal["text"])

    st.configure("TScale", background=pal["panel"], troughcolor=pal["input_bg"],
                 bordercolor=pal["border"], lightcolor=pal["accent"], darkcolor=pal["accent"])

    st.configure("Treeview", background=pal["panel"], fieldbackground=pal["panel"],
                 foreground=pal["text"], bordercolor=pal["border"],
                 rowheight=22, relief="flat")
    st.configure("Treeview.Heading", background=pal["header"], foreground=pal["header_fg"],
                 bordercolor=pal["border"], relief="flat",
                 font=("Microsoft YaHei UI", 9, "bold"), padding=(4, 3))
    # 关键（继承 v24 教训）：移除 ttk 内建 selected 映射，否则会覆盖行 tag 配色。
    st.map("Treeview", background=[], foreground=[])
    st.map("Treeview.Heading", background=[("active", pal["tab_active"])])

    st.configure("Queue.Treeview", background=pal["panel"], fieldbackground=pal["panel"],
                 foreground=pal["text"], bordercolor=pal["border"], rowheight=22)
    st.map("Queue.Treeview", background=[], foreground=[])

    st.configure("TNotebook", background=pal["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=pal["tab_bg"], foreground=pal["text_dim"],
                 bordercolor=pal["border"], padding=(12, 4), font=("Microsoft YaHei UI", 9))
    st.map("TNotebook.Tab", background=[("selected", pal["tab_active"]), ("active", pal["tab_bg"])],
           foreground=[("selected", pal["text"])])

    st.configure("TScrollbar", background=pal["header"], troughcolor=pal["panel"],
                 bordercolor=pal["border"], arrowcolor=pal["text_dim"], relief="flat")

    if extra_styles:
        # 停靠系统专用样式
        st.configure("Dock.TFrame", background=pal["panel"])
        st.configure("DockHeader.TFrame", background=pal["header"])
        st.configure("DockHeader.TLabel", background=pal["header"],
                     foreground=pal["header_fg"], font=("Microsoft YaHei UI", 9, "bold"))
        st.configure("DockTab.TFrame", background=pal["tab_bg"])
        st.configure("DockTabActive.TFrame", background=pal["tab_active"])
        st.configure("DockTab.TLabel", background=pal["tab_bg"], foreground=pal["text_dim"])
        st.configure("DockTabActive.TLabel", background=pal["tab_active"],
                     foreground=pal["text"], font=("Microsoft YaHei UI", 9, "bold"))
        st.configure("Status.TLabel", background=pal["chrome"], foreground=pal["text_dim"])
        st.configure("Accent.TLabel", background=pal["panel"], foreground=pal["accent"],
                     font=("Microsoft YaHei UI", 9, "bold"))

    try:
        root.configure(background=pal["bg"])
    except Exception:
        pass
    return pal


# ---------------------------------------------------------------- 系统跟随
class SystemThemeWatcher:
    """定时轮询 Windows 应用模式；变化时回调。"""

    def __init__(self, root, callback, interval_ms=4000):
        self.root = root
        self.callback = callback
        self.interval = interval_ms
        self._last = system_is_light()
        self._job = None

    def start(self):
        self._tick()

    def stop(self):
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _tick(self):
        try:
            cur = system_is_light()
            if cur != self._last:
                self._last = cur
                self.callback(cur)
        except Exception:
            pass
        self._job = self.root.after(self.interval, self._tick)
