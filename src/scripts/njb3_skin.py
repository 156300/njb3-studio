#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""njb3_skin.py — NJB3 工具的界面皮肤（外观）配置。

设计参照 foobar2000「Quick Appearance Setup / Colors and Fonts」：
  · 内置多套预设皮肤（配色 + 字体），一键切换并实时预览；
  · 允许微调单项颜色/字体，保存到 exe 同目录的 gui_skin.json；
  · 支持导入/导出皮肤文件（.json），便于分享与备份。

本模块只负责“皮肤数据 + 应用到 ttk”，不依赖 njb3_gui，便于单独测试。
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- 预设皮肤
# 每套皮肤是一组“语义色”，apply_skin 把它们映射到 ttk 控件与 Treeview tag。
# 字段说明：
#   theme       ttk 主题（clam / alt / default / vista ...）
#   bg          常规背景
#   fg          常规前景（文字）
#   select_bg   当前焦点选中行底（hot）
#   select_fg   当前焦点选中行字
#   idle_bg     非焦点普通树选中行底（cold）
#   idle_fg     非焦点普通树选中行字
#   stage_bg    非焦点播放队列选中行底（warm）
#   stage_fg    非焦点播放队列选中行字
#   accent      强调色（标题/链接/选中标签）
#   ok          正常/成功色（音量等）
#   warn        警示色（状态/未同步）
#   font_family 界面字体族
#   font_size   界面字号
PRESETS = {
    "经典浅色": {
        "theme": "clam",
        "bg": "#FFFFFF", "fg": "#000000",
        "select_bg": "#4a6fa5", "select_fg": "#ffffff",
        "idle_bg": "#d9d9d9", "idle_fg": "#000000",
        "stage_bg": "#FFF3B0", "stage_fg": "#000000",
        "accent": "#1a5fb4", "ok": "#006600", "warn": "#bb0000",
        "font_family": "Microsoft YaHei UI", "font_size": 9,
    },
    "深夜蓝": {
        "theme": "clam",
        "bg": "#1e2430", "fg": "#e6e9ef",
        "select_bg": "#3d6fb4", "select_fg": "#ffffff",
        "idle_bg": "#39404e", "idle_fg": "#d7dbe3",
        "stage_bg": "#5a4a1f", "stage_fg": "#ffe9a8",
        "accent": "#7fb3ff", "ok": "#6fd08c", "warn": "#ff8080",
        "font_family": "Microsoft YaHei UI", "font_size": 9,
    },
    "森林绿": {
        "theme": "clam",
        "bg": "#f3f8f2", "fg": "#18301a",
        "select_bg": "#2f7d4f", "select_fg": "#ffffff",
        "idle_bg": "#cfe4d2", "idle_fg": "#18301a",
        "stage_bg": "#f7e6a8", "stage_fg": "#18301a",
        "accent": "#1c6b3c", "ok": "#0a7a35", "warn": "#a52a2a",
        "font_family": "Microsoft YaHei UI", "font_size": 9,
    },
    "暖阳橙": {
        "theme": "clam",
        "bg": "#fffaf2", "fg": "#3a2a18",
        "select_bg": "#b5651d", "select_fg": "#ffffff",
        "idle_bg": "#efdcc2", "idle_fg": "#3a2a18",
        "stage_bg": "#ffe4a3", "stage_fg": "#3a2a18",
        "accent": "#a85410", "ok": "#3f7d20", "warn": "#c0392b",
        "font_family": "Microsoft YaHei UI", "font_size": 9,
    },
    "高对比": {
        "theme": "clam",
        "bg": "#000000", "fg": "#ffffff",
        "select_bg": "#ffff00", "select_fg": "#000000",
        "idle_bg": "#555555", "idle_fg": "#ffffff",
        "stage_bg": "#00ffff", "stage_fg": "#000000",
        "accent": "#00ff00", "ok": "#00ff00", "warn": "#ff4444",
        "font_family": "Microsoft YaHei UI", "font_size": 10,
    },
}

DEFAULT_SKIN = "经典浅色"
SKIN_VERSION = 1
COLOR_KEYS = ("bg", "fg", "select_bg", "select_fg", "idle_bg", "idle_fg",
              "stage_bg", "stage_fg", "accent", "ok", "warn")
ALL_KEYS = ("theme",) + COLOR_KEYS + ("font_family", "font_size")


def preset_names() -> list[str]:
    return list(PRESETS.keys())


def get_preset(name: str) -> dict:
    src = PRESETS.get(name) or PRESETS[DEFAULT_SKIN]
    return copy.deepcopy(src)


def config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if getattr(sys, "frozen", False):
        base = Path(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base = Path(os.path.dirname(os.path.abspath(__file__)))
    return base / "gui_skin.json"


def default_config() -> dict:
    return {"version": SKIN_VERSION, "name": DEFAULT_SKIN,
            "skin": get_preset(DEFAULT_SKIN)}


def _coerce_color(value, fallback: str) -> str:
    """只接受 #RRGGBB，避免非法值让 ttk 抛异常。"""
    s = str(value or "")
    if len(s) == 7 and s.startswith("#"):
        try:
            int(s[1:], 16)
            return s
        except Exception:
            pass
    return fallback


def normalize_skin(data: dict, base_name: str | None = None) -> dict:
    """把任意字典规范成完整皮肤；未知字段丢弃，缺项用所选预设补齐。"""
    base = get_preset(base_name or DEFAULT_SKIN)
    if not isinstance(data, dict):
        return base
    out = dict(base)
    theme = str(data.get("theme") or base["theme"])
    out["theme"] = theme
    for k in COLOR_KEYS:
        if k in data:
            out[k] = _coerce_color(data.get(k), base[k])
    out["font_family"] = str(data.get("font_family") or base["font_family"])
    try:
        out["font_size"] = min(18, max(7, int(data.get("font_size", base["font_size"]))))
    except Exception:
        out["font_size"] = base["font_size"]
    return out


def load_config(path: str | None = None) -> dict:
    p = config_path(path)
    if not p.exists():
        return default_config()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default_config()
    name = str(data.get("name") or DEFAULT_SKIN)
    raw = data.get("skin")
    skin = normalize_skin(raw if isinstance(raw, dict) else {}, name)
    return {"version": SKIN_VERSION, "name": name, "skin": skin}


def save_config(cfg: dict, path: str | None = None) -> Path:
    p = config_path(path)
    name = str(cfg.get("name") or DEFAULT_SKIN)
    skin = normalize_skin(cfg.get("skin"), name)
    payload = {"version": SKIN_VERSION, "name": name, "skin": skin}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def export_skin(cfg: dict, path: str | Path) -> Path:
    """导出为可导入的皮肤文件（就是 gui_skin.json 同格式）。"""
    p = Path(path)
    if p.suffix.lower() != ".json":
        p = p.with_suffix(".json")
    save_config(cfg, str(p))
    return p


def import_skin(path: str | Path) -> dict:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("皮肤文件格式不正确（顶层应为对象）")
    name = str(data.get("name") or DEFAULT_SKIN)
    raw = data.get("skin", data)      # 兼容直接给出皮肤字典的文件
    return {"version": SKIN_VERSION, "name": name,
            "skin": normalize_skin(raw if isinstance(raw, dict) else {}, name)}


# ---------------------------------------------------------------- 应用皮肤
def apply_skin(root, skin: dict) -> bool:
    """把皮肤应用到 ttk 样式。返回是否成功。

    只设置“语义色到 ttk”的映射；Treeview 的行 tag 由主程序的
    _apply_focus_highlight 负责，这里同步更新 self._skin 供其取色。
    """
    import tkinter.ttk as ttk
    try:
        skin = normalize_skin(skin)
        st = ttk.Style()
        try:
            st.theme_use(skin["theme"])
        except Exception:
            pass

        font = (skin["font_family"], skin["font_size"])
        st.configure(".", background=skin["bg"], foreground=skin["fg"],
                     font=font, fieldbackground=skin["bg"])
        st.configure("Treeview", background=skin["bg"], foreground=skin["fg"],
                     fieldbackground=skin["bg"], bordercolor=skin["idle_bg"])
        st.configure("TLabelframe", background=skin["bg"], foreground=skin["fg"])
        st.configure("TFrame", background=skin["bg"])
        st.configure("TNotebook", background=skin["idle_bg"])
        st.configure("TNotebook.Tab", background=skin["idle_bg"], foreground=skin["fg"])
        st.map("TNotebook.Tab",
               background=[("selected", skin["bg"]), ("active", skin["idle_bg"])],
               foreground=[("selected", skin["accent"])])
        st.configure("TButton", background=skin["idle_bg"], foreground=skin["fg"])
        st.configure("Accent.TButton", background=skin["accent"], foreground="#ffffff")
        # 移除 selected 内建高亮，让行 tag 的 background 生效（v24 根因）
        st.map("Treeview", background=[], foreground=[])
        st.map("Queue.Treeview", background=[], foreground=[])
        try:
            root.configure(background=skin["bg"])
        except Exception:
            pass
        return True
    except Exception:
        return False


def focus_tags(skin: dict) -> dict:
    """供主程序 _apply_focus_highlight 使用的三种行 tag 颜色。"""
    skin = normalize_skin(skin)
    return {
        "hot": (skin["select_bg"], skin["select_fg"]),
        "cold": (skin["idle_bg"], skin["idle_fg"]),
        "warm": (skin["stage_bg"], skin["stage_fg"]),
    }
