#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v45 专项测试：启动默认控制工作台 + 两处单次播放建议。"""
import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
import njb3_gui as base
import njb3_studio as studio

CFG = HERE / "studio_dock.json"
old_cfg = CFG.read_text(encoding="utf-8") if CFG.exists() else None

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name} {detail}")


try:
    print("=== A. 默认工作台 ===")
    tree = studio.studio_default_tree(list(studio.MAIN_IDS))
    chk("默认布局 active=控制工作台", tree.get("active") == "other", str(tree))

    root = tk.Tk()
    app = studio.StudioApp(root)
    root.update()
    tabs = app.dock._find_tabs_of("other")
    chk("启动后 dock 当前页为控制工作台",
        bool(tabs) and tabs.get("active") == "other", str(tabs))
    chk("设计默认树 active=other",
        app.studio_default_layout(list(studio.MAIN_IDS)).get("active") == "other")

    print("=== B. 两处单次播放建议 ===")
    btn_text = str(app.pcmode_btn.cget("text"))
    chk("播控开关按钮含单次播放建议",
        "强烈建议NJB3使用单次播放模式" in btn_text, btn_text)
    chk("播控开关按钮仍保留原功能说明",
        "清空机器内现有选定的音乐" in btn_text, btn_text)

    chk("确认弹窗方法已在新版 UI 覆盖",
        studio.StudioApp.action_pcmode_enable is not base.App.action_pcmode_enable)

    app.connected = True
    app.worker_busy = False
    captured = {}

    def fake_askyesno(title, message, **kw):
        captured["title"] = title
        captured["message"] = message
        return False   # 不真的执行清空

    calls = []
    app._worker = lambda fn, *a, **kw: calls.append((fn, kw))
    with mock.patch.object(studio.messagebox, "askyesno", fake_askyesno):
        app.action_pcmode_enable()
    chk("确认弹窗标题正确", captured.get("title") == "开启播控模式",
        str(captured.get("title")))
    chk("确认弹窗含单次播放建议",
        "强烈建议NJB3使用单次播放模式" in captured.get("message", ""),
        captured.get("message", ""))
    chk("取消确认后不清空队列", not calls, str(calls))

    root.destroy()
finally:
    if old_cfg is not None:
        CFG.write_text(old_cfg, encoding="utf-8")

print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
