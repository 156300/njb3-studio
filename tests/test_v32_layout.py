#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v32 专项测试：三整页工作台 / 资源管理器与传输队列嵌入 / 设备状态两行 /
音量 EAX 合并 / 日志上限 / 控制工作台模块可停靠 / 播控侧栏竖线箭头。"""
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

print("=== 1. 顶栏三按钮 ===")
top = app._chrome_frames[0]
texts = [str(w.cget("text")) for w in top.winfo_children() if "text" in w.keys()]
chk("无可停靠工作台字样", "可停靠工作台" not in " ".join(texts), texts)
labels = [t.strip().lstrip("● ").strip() for t in texts]
chk("三按钮名称", [x for x in labels if x in
                  ("传输工作台", "播控工作台", "控制工作台")] ==
    ["传输工作台", "播控工作台", "控制工作台"], labels)
chk("无资源管理器/传输队列按钮",
    "资源管理器" not in texts and "传输队列" not in texts, texts)
try:
    i_ap = next(i for i, t in enumerate(texts) if "外观" in t)
    i_rs = next(i for i, t in enumerate(texts) if "复位布局" in t)
    i_m = next(i for i, t in enumerate(texts) if "传输工作台" in t)
    i_p = next(i for i, t in enumerate(texts) if "播控工作台" in t)
    i_o = next(i for i, t in enumerate(texts) if "控制工作台" in t)
    i_dc = next(i for i, t in enumerate(texts) if "断开设备" in t)
    i_cn = next(i for i, t in enumerate(texts) if t == "连接设备")
    i_st = max(i for i, t in enumerate(texts) if "连接" in t or "未连接" in t)
    chk("顶栏顺序", i_ap < i_rs < i_m < i_p < i_o < i_dc < i_cn < i_st, texts)
except StopIteration:
    chk("顶栏顺序", False, texts)
for w in top.winfo_children():
    if "text" in w.keys():
        chk(f"靠左 {str(w.cget('text'))[:6]}", w.pack_info().get("side") == "left")

print("=== 2. 三整页工作台（无页签、无眉头）===")
chk("三个主面板", sorted(app.dock.panels) == ["machine", "other", "playctrl"],
    str(list(app.dock.panels)))
chk("整页无标题栏", all(p.header is None for p in app.dock.panels.values()))
chk("隐藏页签栏", app.dock.tree.get("show_tabs") is False)
chk("无可视页签栏控件", len(app.dock._tab_frames) == 0, str(len(app.dock._tab_frames)))
chk("默认显示控制工作台（v45）",
    app.dock.panels["other"].outer.winfo_ismapped() == 1
    and app.dock.panels["machine"].outer.winfo_ismapped() == 0
    and app.dock.panels["playctrl"].outer.winfo_ismapped() == 0)
app.action_show_main("playctrl")
root.update()
chk("切到播控工作台",
    app.dock.tree["active"] == "playctrl"
    and app.dock.panels["playctrl"].outer.winfo_ismapped() == 1)
app.action_show_main("other")
root.update()
chk("切到控制工作台", app.dock.tree["active"] == "other")
app.action_show_main("machine")
root.update()

print("=== 3. 资源管理器 / 传输队列嵌入传输工作台 ===")
mb = app.dock.panels["machine"].body
for attr in ("folder_tree", "file_tree", "lib_tree2", "tree", "start_btn"):
    chk(f"{attr} 在传输工作台内", inside(getattr(app, attr), mb), "")
chk("传输工作台为三栏", len(app.workbench_paned.panes()) == 3,
    str(len(app.workbench_paned.panes())))
_panes = app.workbench_paned.panes()
chk("资源管理器在最左",
    str(app.explorer_frame).startswith(_panes[0])
    and str(app.transfer_body).startswith(_panes[2]))
app.action_explorer_fold(); root.update()
chk("资源管理器可左缩进（竖线三角）", app.explorer_body.winfo_ismapped() == 0
    and "explorer" in app._wb_handles)
app.action_explorer_fold(); root.update()
app.action_transfer_fold(); root.update()
chk("传输队列可右缩进（竖线三角）", app.transfer_body.winfo_ismapped() == 0
    and "transfer" in app._wb_handles)
app.action_transfer_fold(); root.update()

print("=== 4. 设备状态两行 + 音量/EAX 合并 ===")
chk("设备状态为独立模块", "devstat" in app.control_dock.panels)
chk("音量在设备状态内", inside(app.vol_scale, app.info_bar))
chk("EAX 在设备状态内", inside(app.eax_chk, app.info_bar))
chk("信息四项在右侧容器", all(inside(w, app.info_right)
                              for w in app.info_labels.values()))
grid = {k: (w.grid_info().get("row"), w.grid_info().get("column"))
        for k, w in app.info_labels.items()}
chk("右侧四项一行（v33）", sorted(set(r for r, _c in grid.values())) == [0], str(grid))
chk("顺序为容量/可用/电源/固件",
    [grid[k] for k in ("cap", "free", "bat", "fw")] == [(0, 0), (0, 1), (0, 2), (0, 3)],
    str(grid))
ctl_titles = [p.title for p in app.control_dock.panels.values()]
chk("无独立音量/音效栏", not any("音量" in t or "音效" in t for t in ctl_titles),
    str(ctl_titles))

print("=== 5. 日志列上限 + 模块可停靠 ===")
cd = app.control_dock
rnode = cd.tree
# v37：控制页改为左右分布，日志固定在右列；上限 45%，左列模块最小 55%。
chk("日志节点有上限", rnode.get("max_ratios") == [1.0, 0.45], str(rnode.get("max_ratios")))
chk("模块最小比例", rnode.get("min_ratio") == 0.12, str(rnode.get("min_ratio")))
cd._sash_drag = (rnode, 0, 100000, 0, list(rnode["ratios"]))


class _E:
    x_root = 100000
    y_root = 0


cd._sash_motion(_E())
cd._sash_release()
chk("日志宽不超过 45%", rnode["ratios"][1] <= 0.4501, str(rnode["ratios"]))
chk("六个内部模块", sorted(cd.panels) ==
    ["appearance", "devstat", "driver", "log", "playback", "refresh"], str(list(cd.panels)))
chk("模块有拖动标题", all(p.header is not None for p in cd.panels.values()))
cd.dock_to("refresh", "devstat", "center")
root.update()
chk("模块可合并/贴边重排", "refresh" in cd._find_tabs_of("devstat")["panels"])
cd.tree = studio.control_default_tree(list(cd.panels))
cd.relayout()

print("=== 6. 播控侧栏竖线 + 箭头 ===")
app.action_show_main("playctrl")
root.update()
app.action_pc_fold_left()
root.update()
chk("左侧折叠只留竖线与箭头", "left" in app._pc_handles
    and app.pc_left_body.winfo_ismapped() == 0)
line, arrow = app._pc_handles["left"]
chk("箭头按钮可展开", str(arrow.cget("text")) == "▶", str(arrow.cget("text")))
chk("竖线存在", line.winfo_exists() == 1)
app.action_pc_unfold_left()
root.update()
chk("展开后竖线移除", "left" not in app._pc_handles
    and app.pc_left_body.winfo_ismapped() == 1)
app.action_pc_fold_right(); root.update()
chk("右侧折叠箭头", "right" in app._pc_handles
    and str(app._pc_handles["right"][1].cget("text")) == "◀")
app.action_pc_unfold_right(); root.update()

for cfg in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if cfg.exists():
            cfg.unlink()
    except Exception:
        pass
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
