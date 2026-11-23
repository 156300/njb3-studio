#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v34 专项测试：题头位置、折叠按钮、上下可拖拽、最小尺寸、控制页菜单移除。"""
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


def texts(widget):
    out = []
    for w in widget.winfo_children():
        if "text" in w.keys():
            out.append(str(w.cget("text")))
        out.extend(texts(w))
    return out


def drag_split(app, attr, dy):
    """直接调用分隔条的拖动逻辑，避免合成鼠标事件受窗口位置影响。"""
    sash = getattr(app, attr)

    class Ev:
        y_root = 0

    sash._split_press(Ev())
    Ev.y_root = dy
    sash._split_motion(Ev())
    sash._split_release(Ev())
    app.root.update()


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.update()
time.sleep(0.2)
root.update()

print("=== 1. 资源管理器：贴顶 + 最右折叠按钮 ===")
chk("目录/文件标题行贴顶（labelwidget）",
    str(app.explorer_frame.cget("labelwidget")) == str(app.explorer_title),
    str(app.explorer_frame.cget("labelwidget")))
chk("标题含资源管理器 目录 / 文件",
    any("资源管理器 目录 / 文件" in t for t in texts(app.explorer_title)),
    str(texts(app.explorer_title)))
chk("折叠按钮在标题行最右",
    str(app.explorer_fold_btn.pack_info().get("side")) == "right")
chk("折叠按钮初始为 «", str(app.explorer_fold_btn.cget("text")) == "«")

print("=== 2. 传输队列：题头左侧折叠按钮 ===")
head_children = app.transfer_head.winfo_children()
chk("题头第一个控件是折叠按钮",
    isinstance(head_children[0], ttk.Button)
    and str(head_children[0].cget("text")) == "»", str(texts(app.transfer_head)))
chk("折叠按钮在“传输队列”文字左侧",
    "传输队列" in str(head_children[1].cget("text")))
app.action_transfer_fold(); root.update()
chk("折叠后按钮变 «", str(app.transfer_fold_btn.cget("text")) == "«")
app.action_transfer_fold(); root.update()
chk("展开后按钮变 »", str(app.transfer_fold_btn.cget("text")) == "»")

print("=== 3. 传输工作台：机器内歌曲标题在搜索条上方 ===")
left = app.lib_tree2.master.master
order = [w.winfo_class() for w in left.pack_slaves()]
chk("标题行 → 搜索条 → 列表框体",
    order[:3] == ["TFrame", "TFrame", "TLabelframe"], str(order))
chk("标题行为机器内歌曲点表头排序",
    "机器内歌曲（点表头排序" in str(left.pack_slaves()[0].winfo_children()[0].cget("text")))

print("=== 4. 播放清单上下可拖拽（两个工作台） ===")
for label, attr, top, bottom, top_min, bot_min in (
    ("播控", "pc_left_split", app.pl_tree.master, app.pl_songs.master, 92, 120),
    ("传输", "transfer_playlist_split", app.pl_tree2.master,
     app.pl_songs2.master, 92, 120),
):
    app.action_show_main("playctrl" if label == "播控" else "machine")
    root.update()
    time.sleep(0.25)
    root.update()
    sash = getattr(app, attr)
    chk(f"{label}分隔条存在且为上下箭头",
        sash.winfo_exists() == 1 and sash.cget("cursor") == "sb_v_double_arrow")
    drag_split(app, attr, 10000)
    h_top, h_bot = top.winfo_height(), bottom.winfo_height()
    chk(f"{label}向下拖到底仍不遮挡内容",
        h_top >= top_min and h_bot >= bot_min, f"{h_top}/{h_bot}")
    drag_split(app, attr, -10000)
    h_top, h_bot = top.winfo_height(), bottom.winfo_height()
    chk(f"{label}向上拖到底仍不遮挡内容",
        h_top >= top_min and h_bot >= bot_min, f"{h_top}/{h_bot}")

print("=== 5. 播控工作台：标题行按钮位置与搜索顺序 ===")
pf = app.pl_tree.master
chk("播放清单折叠按钮与标题同行", inside(app.pc_left_fold_btn, pf)
    and str(pf.cget("labelwidget")) == str(app.pc_left_fold_btn.master))
chk("播放清单底部四按钮已删",
    not any(isinstance(w, ttk.Button) and str(w.cget("text")) in ("新建", "改名", "刷新", "删除")
            for w in pf.winfo_children()))
lf = app.lib_tree.master
hdr = app.pc_right_fold_btn.master
chk("机器内歌曲折叠按钮在标题行最左",
    inside(hdr, lf) and str(lf.cget("labelwidget")) == str(hdr)
    and str(app.pc_right_fold_btn.pack_info().get("side")) == "left")
chk("标题文字为机器内歌曲拖到中间",
    any("机器内歌曲（拖到中间" in t for t in texts(hdr)), str(texts(hdr)))
sb_row = int(app.search_bar.grid_info().get("row", 99))
tree_row = int(app.lib_tree.grid_info().get("row", -1))
chk("搜索条在标题下一行、列表上方", sb_row == 0 and tree_row == 1,
    f"{sb_row}/{tree_row}")

print("=== 6. 控制工作台：取消所有小框体菜单和悬浮按钮 ===")
cd = app.control_dock
chk("配置关闭菜单与按钮", cd.allow_panel_menu is False and cd.show_panel_buttons is False)
for pid, panel in cd.panels.items():
    chk(f"{pid} 无三点菜单与浮动按钮",
        panel.btn_menu is None and panel.btn_float is None)
    binds = panel.header.bind()
    chk(f"{pid} 标题栏无右键绑定", "<Button-3>" not in binds, str(binds))

print("=== 7. 最小尺寸：传输队列“查看/传输”行 ===")
ops = next(root.nametowidget(w) for w in app.transfer_paned.panes()
           if any("查看:" in texts(c) for c in root.nametowidget(w).winfo_children()))
minsize = app.transfer_paned.paneconfigure(ops, "minsize")
chk("操作区最小高度 >= 132", int(minsize[-1]) >= 132, str(minsize))
chk("查看/传输行存在", any("查看:" in t for t in texts(ops)), str(texts(ops)))

print("=== 8. 最小尺寸：控制工作台播放控制按钮 ===")
app.action_show_main("other")
root.update()
root_node = cd.tree
playback_node = cd.find_panel_parent("playback")[0]
chk("播放控制所在分割节点有 min_px",
    bool(playback_node and playback_node.get("min_px")), str(playback_node))
# 模拟把播放控制往上拖到极限：相邻块不能挤掉按钮行。
node, idx = cd.find_panel_parent("playback")
if node and idx is not None:
    rect = cd._node_rects.get(id(node))
    if rect:
        cd._sash_drag = (node, idx, 0, rect[1], list(node["ratios"]))
        cd._sash_motion(type("E", (), {"x_root": 0, "y_root": rect[1] - 100000})())
        cd._sash_release()
        root.update()
btn = app.ctrl_btn_row.winfo_children()[0]
chk("播放按钮仍完整显示", btn.winfo_ismapped() == 1
    and btn.winfo_height() > 0, f"{btn.winfo_height()}")

for cfg in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if cfg.exists():
            cfg.unlink()
    except Exception:
        pass
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
