#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""njb3_layout.py — GUI 布局配置的读取、校验与默认值。

布局编辑器与主程序共用这个文件。配置默认放在 njb3_gui.exe 同目录的
gui_layout.json；文件不存在时全部使用默认值，主程序行为与上一版一致。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

DEFAULT_LAYOUT = {
    "version": 2,
    "explorer_mode": "native",          # native | legacy
    "auto_refresh_on_connect": True,
    "show_progress_dialog": True,
    "show_lib_search": True,
    "sash": {
        "main_explorer": 0.32,          # 左侧资源管理器占总宽比例
        "machine_lib": 0.62,            # 机器歌曲区占机器页签宽度比例
        "other_log": 0.60,              # 上半功能区占其他页签高度比例
        "playctrl_left": 0.26,          # 播放控制页：左侧“播放列表+列表内歌曲”占总宽度比例
        "playctrl_mid": 0.55,           # 播放控制页：中间栏占“除去左栏后”的宽度比例
    },
    "tab_order": ["播放控制", "机器歌曲", "传输列表", "其他功能"],
    "lib_columns": [
        {"key": "num", "title": "#", "width": 42, "visible": True},
        {"key": "id", "title": "ID", "width": 60, "visible": True},
        {"key": "title", "title": "标题", "width": 200, "visible": True},
        {"key": "artist", "title": "艺人", "width": 110, "visible": True},
        {"key": "album", "title": "专辑", "width": 130, "visible": True},
        {"key": "genre", "title": "流派", "width": 90, "visible": True},
        {"key": "folder", "title": "文件夹", "width": 130, "visible": True},
        {"key": "size", "title": "大小", "width": 80, "visible": True},
        {"key": "length", "title": "时长", "width": 60, "visible": True},
        {"key": "trackno", "title": "曲号", "width": 50, "visible": True},
    ],
    "queue_columns": [
        {"key": "#", "title": "#", "width": 34, "visible": True},
        {"key": "status", "title": "状态", "width": 62, "visible": True},
        {"key": "title", "title": "标题", "width": 210, "visible": True},
        {"key": "artist", "title": "艺人", "width": 110, "visible": True},
        {"key": "album", "title": "专辑", "width": 130, "visible": True},
        {"key": "folder", "title": "分类文件夹", "width": 130, "visible": True},
        {"key": "size", "title": "大小", "width": 78, "visible": True},
        {"key": "progress", "title": "进度", "width": 110, "visible": True},
    ],
}

VALID_TABS = ("播放控制", "机器歌曲", "传输列表", "其他功能")
VALID_LIB_KEYS = tuple(c["key"] for c in DEFAULT_LAYOUT["lib_columns"])
VALID_QUEUE_KEYS = tuple(c["key"] for c in DEFAULT_LAYOUT["queue_columns"])


def default_layout() -> dict:
    return copy.deepcopy(DEFAULT_LAYOUT)


def config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if getattr(sys, "frozen", False):
        base = Path(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base = Path(os.path.dirname(os.path.abspath(__file__)))
    return base / "gui_layout.json"


def _merge_default(data: dict) -> dict:
    out = default_layout()
    if not isinstance(data, dict):
        return out
    for key in ("explorer_mode", "auto_refresh_on_connect", "show_progress_dialog",
                "show_lib_search"):
        if key in data:
            out[key] = data[key]
    out["explorer_mode"] = "legacy" if out["explorer_mode"] == "legacy" else "native"
    for key in ("auto_refresh_on_connect", "show_progress_dialog", "show_lib_search"):
        out[key] = bool(out[key])
    sash = data.get("sash")
    if isinstance(sash, dict):
        for key in out["sash"]:
            try:
                val = float(sash.get(key, out["sash"][key]))
                out["sash"][key] = min(0.85, max(0.15, val))
            except Exception:
                pass
    tabs = data.get("tab_order")
    if isinstance(tabs, list):
        clean = [t for t in tabs if t in VALID_TABS]
        for t in VALID_TABS:
            if t not in clean:
                clean.append(t)
        out["tab_order"] = clean[:len(VALID_TABS)]
    for group, valid in (("lib_columns", VALID_LIB_KEYS),
                         ("queue_columns", VALID_QUEUE_KEYS)):
        items = data.get(group)
        if not isinstance(items, list):
            continue
        by_key = {c["key"]: c for c in items if isinstance(c, dict)
                  and c.get("key") in valid}
        merged = []
        for spec in out[group]:
            item = by_key.get(spec["key"])
            if not item:
                merged.append(spec)
                continue
            copy_spec = dict(spec)
            try:
                copy_spec["width"] = min(600, max(30, int(item.get("width", spec["width"]))))
            except Exception:
                pass
            # 固定序号列永远显示；其它列可由编辑器隐藏。
            if spec["key"] != "#" and spec["key"] != "num":
                copy_spec["visible"] = bool(item.get("visible", True))
            merged.append(copy_spec)
        # 允许编辑器改列顺序；补齐遗漏的列。
        order = [c.get("key") for c in items if isinstance(c, dict)
                 and c.get("key") in valid]
        ordered = []
        for key in order:
            found = next((c for c in merged if c["key"] == key), None)
            if found is not None and found not in ordered:
                ordered.append(found)
        for c in merged:
            if c not in ordered:
                ordered.append(c)
        out[group] = ordered
    return out


def load_layout(path: str | None = None) -> dict:
    p = config_path(path)
    if not p.exists():
        return default_layout()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default_layout()
    return _merge_default(data)


def save_layout(data: dict, path: str | None = None) -> Path:
    p = config_path(path)
    cleaned = _merge_default(data)
    p.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
