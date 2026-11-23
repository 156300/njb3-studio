#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v31 保留行为回归（适配 v32）：命名、传输队列底部两行、六播控按钮、
连接状态位置、原播控工作台框体隐藏。v32 新布局见 test_v32_layout.py。"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
from tkinter import ttk
import njb3_studio as studio

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name} {detail}")


def inside(widget, ancestor):
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = getattr(w, "master", None)
    return False


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.lift()
root.update()
time.sleep(0.2)
root.update()

print("=== 1. 命名与整页 ===")
titles = {pid: p.title for pid, p in app.dock.panels.items()}
chk("播控工作台改名", titles["playctrl"] == "播控工作台", titles["playctrl"])
chk("传输工作台改名", titles["machine"] == "传输工作台", titles["machine"])
chk("控制工作台改名", titles["other"] == "控制工作台", titles["other"])
chk("无可停靠字样", "可停靠工作台" not in root.title())
chk("顶部三按钮", sorted(app.main_view_buttons) == ["machine", "other", "playctrl"])

print("=== 2. 顶栏顺序与状态位置 ===")
top = app._chrome_frames[0]
texts = [str(w.cget("text")) for w in top.winfo_children() if "text" in w.keys()]
i_ap = next(i for i, t in enumerate(texts) if "外观" in t)
i_rs = next(i for i, t in enumerate(texts) if "复位布局" in t)
i_m = next(i for i, t in enumerate(texts) if "传输工作台" in t)
i_dc = next(i for i, t in enumerate(texts) if "断开设备" in t)
i_cn = next(i for i, t in enumerate(texts) if t == "连接设备")
i_st = max(i for i, t in enumerate(texts) if "未连接" in t or "已连接" in t)
chk("顶栏顺序", i_ap < i_rs < i_m < i_dc < i_cn < i_st, texts)
chk("状态在连接按钮右侧",
    top.winfo_children()[i_st].winfo_x() > top.winfo_children()[i_cn].winfo_x())
for w in top.winfo_children():
    if "text" in w.keys():
        chk(f"靠左 {str(w.cget('text'))[:6]}", w.pack_info().get("side") == "left")

print("=== 3. 控制工作台播控 ===")
chk("播放控制在控制工作台", inside(app.ctrl_frame, app.dock.panels["other"].outer))
chk("原播控框体隐藏", app._playctrl_orig_frame.winfo_ismapped() == 0)
chk("播控按钮 6 个", len(getattr(app, "_ctrl_btn_widgets", [])) == 6)
app._update_position_title(75)
root.update()
chk("位置显示在标题", "01:15" in str(app.ctrl_frame.cget("text")),
    str(app.ctrl_frame.cget("text")))

print("=== 4. 传输队列底部两行 ===")
chk("队列为上下分割", str(app.transfer_paned.cget("orient")) == "vertical")
view_row = app.view_combo.master
chk("查看/传输同行", app.start_btn.master is view_row and app.stop_btn.master is view_row)
view_text = " ".join(str(w.cget("text")) for w in view_row.winfo_children()
                     if "text" in w.keys())
chk("第二行含查看与传输", "查看" in view_text and "传输" in view_text, view_text)
op_row = app.view_combo.master.master.winfo_children()[0]
op_text = " ".join(str(w.cget("text")) for w in op_row.winfo_children()
                   if isinstance(w, ttk.Button))
chk("第一行六操作按钮",
    all(t in op_text for t in ("添加 MP3", "移除选中", "清空列表", "上移", "下移", "导入 m3u")),
    op_text)

for cfg in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if cfg.exists():
            cfg.unlink()
    except Exception:
        pass
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
