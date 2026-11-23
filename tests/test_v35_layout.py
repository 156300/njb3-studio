#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v35 专项测试：目录双击、文件拖放、目标清单、播放清单拖入、播控模式列与按钮。"""
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


def row_with(app, text):
    for w in app.pc_mode_body.winfo_children():
        if isinstance(w, ttk.Frame) and any(
                "text" in c.keys() and text in str(c.cget("text"))
                for c in w.winfo_children()):
            return w
    return None


class Ev:
    x = 10
    y = 10
    x_root = 0
    y_root = 0


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.update()
time.sleep(0.2)
root.update()
# v45：启动默认停在控制工作台，本测试操作传输工作台的资源管理器，先切页。
app.action_show_main("machine")
root.update()

print("=== 1. 资源管理器目录树双击展开 / 收起 ===")
t = app.folder_tree
iid = "__pc__"
bbox = t.bbox(iid)
Ev.x = bbox[0] + 20
Ev.y = bbox[1] + 5
initial = int(t.item(iid, "open"))
app.on_explorer_double(Ev())
root.update()
chk("双击一次切换状态", int(t.item(iid, "open")) == 1 - initial)
app.on_explorer_double(Ev())
root.update()
chk("再双击一次切回", int(t.item(iid, "open")) == initial)

print("=== 2. 文件拖放：曲库与清单 ===")
real_containing = root.winfo_containing
orig_add = app._add_files_to_queue


def fake_add(files):
    for f in files:
        app.queue_rows.append({"path": f, "tags": {"title": f},
                               "status": "等待", "progress": 0})
    app._render_queue()


app._add_files_to_queue = fake_add
app.playlists = [{"playlistid": 7, "name": "测试清单"}]
app.cur_playlist = 7
if not app.pl_tree2.exists("pl::7"):
    app.pl_tree2.insert("", "end", iid="pl::7", text="测试清单")
app.pl_tree2.selection_set("pl::7")

# 拖到传输工作台的机器内歌曲列表：只入队，不设目标清单
app.queue_rows = []
app._drag_active = True
app._drag_paths = ["a.mp3"]
root.winfo_containing = lambda x, y: app.lib_tree2
app.on_file_drag_release(Ev())
chk("拖到机器内歌曲 → 加入传输队列", len(app.queue_rows) == 1)
chk("机器内歌曲不设目标清单", app.queue_rows[0].get("target_pl_id") is None)

# 拖到传输工作台的清单内歌曲：入队并记录目标清单
app.queue_rows = []
app._drag_active = True
app._drag_paths = ["b.mp3"]
root.winfo_containing = lambda x, y: app.pl_songs2
app.on_file_drag_release(Ev())
chk("拖到清单内歌曲 → 加入传输队列", len(app.queue_rows) == 1)
chk("记录目标清单 ID", app.queue_rows[0].get("target_pl_id") == 7)
chk("记录目标清单名称", app.queue_rows[0].get("target_pl_name") == "测试清单")

# 拖到播控工作台的清单内歌曲
app.queue_rows = []
app._drag_active = True
app._drag_paths = ["c.mp3"]
root.winfo_containing = lambda x, y: app.pl_songs
app.on_file_drag_release(Ev())
chk("播控侧清单内歌曲也能接收拖放",
    len(app.queue_rows) == 1 and app.queue_rows[0].get("target_pl_id") == 7)

print("=== 3. 传输队列“目标清单”列 ===")
chk("列包含 target_pl", "target_pl" in app.tree["columns"])
chk("表头为目标清单",
    any(str(app.tree.heading(c, "text")) == "目标清单" for c in app.tree["columns"]))
chk("行内显示清单名称",
    any("测试清单" in str(app.tree.item(i, "values")) for i in app.tree.get_children()))

print("=== 4. 传输成功后写入对应播放清单 ===")
captured = []
real_worker = app._worker
app._worker = lambda fn, **kw: captured.append(fn)
app.queue_rows = [{"path": "d.mp3", "tags": {"title": "D"}, "status": "等待",
                   "progress": 0, "target_pl_id": 7, "target_pl_name": "测试清单"}]
app.action_start_transfer()
chk("传输任务已提交", len(captured) == 1)


class FakeDev:
    def upload_track(self, path, tags, progress=None):
        if progress:
            progress(1, 1)
        return 999

    def add_tracks_to_playlist(self, plid, ids):
        captured.append(("pl", plid, ids))
        return 1


captured[0](FakeDev())
chk("上传后写入播放清单 7 / 曲目 999",
    ("pl", 7, [999]) in captured, str(captured))
app._worker = real_worker

print("=== 5. 机器内歌曲 → 清单内歌曲（两套树） ===")
orig_lib_tree = app.lib_tree
for label, songs, tree2 in (("传输侧", app.pl_songs2, True),
                            ("播控侧", app.pl_songs, False)):
    calls = []
    app._worker = lambda fn, **kw: calls.append(fn)
    app.lib_tree = app.lib_tree2 if tree2 else orig_lib_tree
    app._pl_drag_ids = [101]
    app.cur_playlist = 7
    root.winfo_containing = lambda x, y: songs
    app.on_lib_drag_release(Ev())

    class D:
        def add_tracks_to_playlist(self, pid, ids):
            calls.append(("call", pid, ids))
            return 1

        def list_playlists(self):
            return app.playlists

    if calls:
        calls[0](D())
    chk(f"{label}拖入清单生效", ("call", 7, [101]) in calls, str(calls))
app._worker = real_worker
root.winfo_containing = real_containing
app._add_files_to_queue = orig_add

print("=== 6. 播控模式：列与锁定序号 ===")
cols = tuple(app.stage_tree["columns"])
chk("移除机器序号列", "mseq" not in cols, str(cols))
chk("新增状态列", "state" in cols)
chk("列顺序 # / 状态 / 标题 / 艺人 / 时长",
    cols == ("num", "state", "title", "artist", "length"), str(cols))
app.tracks = [{"trackid": 1, "title": "A"}, {"trackid": 2, "title": "B"},
              {"trackid": 3, "title": "C"}]
app._set_stage_from_ids([1, 2, 3])
chk("# 默认 1、2、3",
    [app.stage_tree.item(i, "values")[0] for i in app.stage_tree.get_children()]
    == ["1", "2", "3"])
app.stage_tracks[0], app.stage_tracks[1] = app.stage_tracks[1], app.stage_tracks[0]
app._render_stage()
chk("# 始终按位置锁定为 1、2、3",
    [app.stage_tree.item(i, "values")[0] for i in app.stage_tree.get_children()]
    == ["1", "2", "3"])
app.play_state = "playing"
app.play_queue_pos = 0
app._render_stage()
chk("播放中显示播放符号",
    app.stage_tree.item(app.stage_tree.get_children()[0], "values")[1] == "▶")
app.play_state = "paused"
app._render_stage()
chk("暂停时显示暂停符号",
    app.stage_tree.item(app.stage_tree.get_children()[0], "values")[1] == "⏸")

print("=== 7. 播控模式：按钮精简与重排 ===")
all_txt = texts(app.pc_mode_body)
for gone in ("读取清单", "载入机器清单", "加入选中曲目", "机器清单",
             "写回队列", "重刷队列"):
    chk(f"已删除“{gone}”", not any(gone in x for x in all_txt), str(all_txt))
qrow2 = row_with(app, "GO")
qrow3 = row_with(app, "播放模式")
chk("清空队列/舞台按键/按键映射在播放模式行",
    qrow3 is not None and all(any(x in str(c.cget("text"))
                              for c in qrow3.winfo_children() if "text" in c.keys())
                              for x in ("清空队列", "舞台按键", "按键映射")))
chk("播放模式行在按钮行之后",
    app.pc_mode_body.winfo_children().index(qrow3)
    > app.pc_mode_body.winfo_children().index(qrow2))
for label, row in (("按钮行", qrow2), ("模式行", qrow3)):
    widths = [str(c.cget("width")) for c in row.winfo_children()
              if isinstance(c, (ttk.Button, ttk.Checkbutton))]
    chk(f"{label}按钮统一 9 宽", widths and all(w == "9" for w in widths), str(widths))

print("=== 8. 停止按钮后新增音量 / EAX（原控件保留） ===")
qrow2_children = list(qrow2.winfo_children())
labels = [str(c.cget("text")) for c in qrow2_children if "text" in c.keys()]
chk("停止在音量之前", labels.index("■ 停止") < len(labels), str(labels))
chk("新增音量滑块存在", hasattr(app, "vol2_scale") and app.vol2_scale.winfo_exists() == 1)
chk("新增 EAX 开关存在", hasattr(app, "eax2_chk") and app.eax2_chk.winfo_exists() == 1)
chk("控制工作台原音量仍在", inside(app.vol_scale, app.info_bar))
chk("控制工作台原 EAX 仍在", inside(app.eax_chk, app.info_bar))
chk("两套控件共享音量变量",
    str(app.vol2_scale.cget("variable")) == str(app.vol_scale.cget("variable")))
chk("两套控件共享 EAX 变量",
    str(app.eax2_chk.cget("variable")) == str(app.eax_chk.cget("variable")))

for cfg in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if cfg.exists():
            cfg.unlink()
    except Exception:
        pass
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
