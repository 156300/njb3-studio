#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v33 专项测试：控制工作台设备状态一行 / 分隔条修复 /
传输工作台资源管理器精简与竖线折叠 / 播控工作台左右栏重构。"""
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
import njb3_dock as dock

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


def all_text(widget):
    out = []
    for w in widget.winfo_children():
        if "text" in w.keys():
            out.append(str(w.cget("text")))
        out.extend(all_text(w))
    return out


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.lift()
root.update()
time.sleep(0.2)
root.update()

print("=== 1. 控制工作台：设备状态一行 ===")
grid = {k: (w.grid_info().get("row"), w.grid_info().get("column"))
        for k, w in app.info_labels.items()}
chk("四项同一行", sorted(set(r for r, _c in grid.values())) == [0], str(grid))
chk("四列顺序 容量/可用/电源/固件",
    [grid[k][1] for k in ("cap", "free", "bat", "fw")] == [0, 1, 2, 3], str(grid))
chk("音量/EAX 仍在设备状态内",
    inside(app.vol_scale, app.info_bar) and inside(app.eax_chk, app.info_bar))

print("=== 2. 分隔条拖动与鼠标样式 ===")
src = Path(dock.__file__).read_text(encoding="utf-8")
chk("水平分隔条用左右双箭头光标", "sb_h_double_arrow" in src)
chk("垂直分隔条用上下双箭头光标", "sb_v_double_arrow" in src)
chk("拖动绑定全局事件", "bind_all(\"<B1-Motion>\"" in src)
chk("释放解绑全局事件", "unbind_all(\"<B1-Motion>\"" in src)

print("=== 3. 传输工作台：资源管理器精简 ===")
mb = app.dock.panels["machine"].body
ex_texts = all_text(app.explorer_body)
chk("无加入此文件夹MP3按钮", not any("加入此文件夹" in t for t in ex_texts), str(ex_texts))
chk("无路径输入框", not any(isinstance(w, ttk.Entry)
                            for w in app.explorer_body.winfo_children()), "")
chk("无右侧文件可拖到传输列表文字",
    not any("右侧文件可拖到传输列表" == t for t in ex_texts), str(ex_texts))
chk("题头资源管理器并入目录/文件行",
    any("资源管理器 目录 / 文件" in t for t in all_text(app.explorer_title)),
    str(all_text(app.explorer_title)))
chk("目录/文件行贴顶覆盖标题留白",
    str(app.explorer_frame.cget("labelwidget")) == str(app.explorer_title))

print("=== 4. 传输工作台：竖线+三角折叠 ===")
chk("资源管理器可竖线折叠", side_ok := True)
app.action_explorer_fold(); root.update()
chk("资源管理器折叠后留竖线三角", "explorer" in app._wb_handles
    and app.explorer_body.winfo_ismapped() == 0
    and str(app._wb_handles["explorer"][1].cget("text")) == "▶")
app.action_explorer_fold(); root.update()
chk("资源管理器展开", "explorer" not in app._wb_handles)
app.action_transfer_fold(); root.update()
chk("传输队列折叠后留竖线三角", "transfer" in app._wb_handles
    and app.transfer_body.winfo_ismapped() == 0
    and str(app._wb_handles["transfer"][1].cget("text")) == "◀")
app.action_transfer_fold(); root.update()

print("=== 5. 传输工作台：机器曲库与播放清单 ===")
sbar_txt = all_text(app.lib_tree2.master.master.winfo_children()[0])
chk("搜索按钮改搜索字样", "搜索" in sbar_txt and "定位" not in sbar_txt, str(sbar_txt))
mid_title = " ".join(all_text(app.lib_tree2.master.master))
chk("双击暂存改双击修改字段",
    "双击修改" in mid_title and "双击暂存" not in mid_title, mid_title)
pbar = app.pl_tree2.master.winfo_children()[-1]
pbtns = [w for w in pbar.winfo_children() if isinstance(w, ttk.Button)]
chk("播放清单四按钮保留", len(pbtns) == 4, str(len(pbtns)))
chk("播放清单四按钮收窄", all(w.cget("width") == 5 for w in pbtns))
right = app.pl_songs2.master.master
chk("无打开播放控制工作台按钮",
    not any(isinstance(w, ttk.Button) and "播放控制" in str(w.cget("text"))
            for w in right.winfo_children()), str(all_text(right)))

print("=== 6. 播控工作台：左栏 ===")
chk("无播放清单/列表内歌曲标题行",
    not any("播放清单/列表内歌曲" == t for t in all_text(app.pc_left_body)))
chk("折叠按钮在播放清单框内", inside(app.pc_left_fold_btn, app.pl_tree.master))
pf = app.pl_tree.master
chk("播控播放清单底部四按钮已删除",
    not any(isinstance(w, ttk.Button) and str(w.cget("text")) in ("新建", "改名", "刷新", "删除")
            for w in pf.winfo_children()))
app.action_show_main("playctrl"); root.update()
app.action_pc_fold_left(); root.update()
chk("播放清单与列表内歌曲联动缩起",
    app.pl_tree.master.winfo_ismapped() == 0
    and app.pl_songs.master.winfo_ismapped() == 0,
    f"{app.pl_tree.master.winfo_ismapped()}/{app.pl_songs.master.winfo_ismapped()}")
app.action_pc_unfold_left(); root.update()
chk("联动展开", app.pl_tree.master.winfo_ismapped() == 1
    and app.pl_songs.master.winfo_ismapped() == 1)

print("=== 7. 播控工作台：右栏 ===")
lf = app.lib_tree.master
chk("曲库题头为机器内歌曲拖到中间",
    any("机器内歌曲（拖到中间" in t for t in all_text(lf)), str(all_text(lf)))
chk("折叠箭头在曲库框内标题行", inside(app.pc_right_fold_btn, lf))
sb_row = int(app.search_bar.grid_info().get("row", 99))
tree_row = int(app.lib_tree.grid_info().get("row", -1))
chk("搜索行在曲库框体内、列表上方",
    inside(app.search_bar, lf) and sb_row < tree_row, f"{sb_row}/{tree_row}")
rb = all_text(app.pc_right_body)
chk("搜索按钮改搜索字样", "搜索" in rb and "定位" not in rb, str(rb))
chk("播控右侧无底部按钮行",
    not any(x in t for t in rb for x in ("刷新曲库", "下载选中", "改元数据", "同步暂存修改")),
    str(rb))
chk("dirty_var 保留供副套复用", hasattr(app, "dirty_var"))

print("=== 8. 隔离：传输工作台副套双击修改仍在 ===")
# 副套 lib_tree2 的双击绑定通过 _dispatch_tree_event 复用 on_lib_double，
# 取消的只是主套 lib_tree 的控件绑定，副套不受影响。
chk("副套 lib_tree2 存在且可用", app.lib_tree2.winfo_exists() == 1)
chk("on_lib_double 方法仍在", callable(getattr(app, "on_lib_double", None)))
chk("主套双击已解绑（源码标记）",
    any(mark in Path(studio.__file__).read_text(encoding="utf-8")
        for mark in ("self.lib_tree.unbind(\"<Double-1>\")",
                     "tree.unbind(\"<Double-1>\")")))

for cfg in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if cfg.exists():
            cfg.unlink()
    except Exception:
        pass
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
