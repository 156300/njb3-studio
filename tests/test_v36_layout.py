#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v36 专项测试：目录双击、排序、传输进度/清理、清单写入、GO 与音量位置。"""
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
import njb3_gui as base

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
root.update()
time.sleep(0.3)
root.update()
# v45：启动默认停在控制工作台，本测试操作传输工作台的资源管理器，先切页。
app.action_show_main("machine")
root.update()

print("=== 1. 资源管理器双击与排序 ===")
t = app.folder_tree
iid = t.get_children("__pc__")[0]
bbox = t.bbox(iid)


class Ev:
    x = bbox[0] + 40
    y = bbox[1] + 8


initial = int(t.item(iid, "open"))
app.on_explorer_double(Ev())
root.update()
chk("双击展开", int(t.item(iid, "open")) == (not initial))
app.on_explorer_double(Ev())
root.update()
chk("再次双击收起", int(t.item(iid, "open")) == initial)
keys = sorted(["banana", "Apple", "中文", "Zebra", "apple"], key=app._sort_key_text)
chk("英文大小写混排后再排中文", keys == ["Apple", "apple", "banana", "Zebra", "中文"], str(keys))
chk("标题贴顶无留白", str(app.explorer_frame.cget("labelwidget")) == str(app.explorer_title))

print("=== 2. 传输弹窗进度与完成处理 ===")
chk("进度变量存在", hasattr(app, "progress_stat_var"))
app._handle(("progress_stat", "进度 75%   3/24"))
chk("进度显示在首行右侧变量", app.progress_stat_var.get() == "进度 75%   3/24")

events = []
real_put = app.q.put
app.q.put = lambda item: events.append(item)
app.tracks = [{"trackid": 11, "filename": "old.mp3", "filesize": 100}]
app.queue_rows = [
    {"path": "new.mp3", "tags": {"title": "N", "size": 200, "filename": "new.mp3"},
     "status": base.STATUS_WAIT, "progress": 0, "target_pl_id": 7, "target_pl_name": "清单A"},
    {"path": "old.mp3", "tags": {"title": "O", "size": 100, "filename": "old.mp3"},
     "status": base.STATUS_WAIT, "progress": 0, "target_pl_id": 7, "target_pl_name": "清单A"},
]
import os
import tempfile
tmp = tempfile.mkdtemp()
for name, size in (("new.mp3", 200), ("old.mp3", 100)):
    with open(os.path.join(tmp, name), "wb") as f:
        f.write(b"0" * size)
app.queue_rows[0]["path"] = os.path.join(tmp, "new.mp3")
app.queue_rows[1]["path"] = os.path.join(tmp, "old.mp3")
captured = []
app._worker = lambda fn, **kw: captured.append(fn)


class FakeDev:
    def upload_track(self, path, tags, progress=None):
        if progress:
            progress(150, 200)
        return 999

    def add_tracks_to_playlist(self, plid, ids):
        captured.append(("pl", plid, list(ids)))
        return 8

    def list_playlists(self):
        return [{"playlistid": 8, "name": "清单A"}]


app.action_start_transfer()
chk("传输任务已提交", len(captured) == 1)
captured[0](FakeDev())
stats = [e for e in events if e[0] == "progress_stat"]
chk("进度含百分比与完成数/总数", any("进度 75%   0/2" in str(e[1]) for e in stats), str(stats))
adds = [e for e in captured if isinstance(e, tuple) and e[0] == "pl"]
chk("同一清单只批量写一次", len(adds) == 1 and adds[0][2] == [999, 11], str(adds))
chk("写入后更新新清单 ID", any(e[0] == "playlist_retarget" and e[2] == 8 for e in events))
chk("完成后通知刷新与清空", any(e[0] == "transfer_finished" and e[1] for e in events))
chk("同名同长度文件被跳过", any("已存在同名同长度" in str(e[1]) for e in events if e[0] == "log"))
app.q.put = real_put

print("=== 3. 播放清单拖放与题头 ===")
app.queue_rows = []
app._drag_active = True
app._drag_paths = ["x.mp3"]
app.playlists = [{"playlistid": 7, "name": "测试清单"}]
app.cur_playlist = 7
if not app.pl_tree2.exists("pl::7"):
    app.pl_tree2.insert("", "end", iid="pl::7", text="测试清单")
app.pl_tree2.selection_set("pl::7")
root.winfo_containing = lambda x, y: app.pl_songs2


class DragEv:
    x_root = 0
    y_root = 0


app._add_files_to_queue = lambda files: [
    app.queue_rows.append({"path": f, "tags": {"title": f}, "status": "等待", "progress": 0})
    for f in files
]
app.on_file_drag_release(DragEv())
chk("文件拖入清单区仍记录目标清单", app.queue_rows[0].get("target_pl_id") == 7)

# 拖到播放清单名字行：按行 ID 识别
rowbox = app.pl_tree2.bbox("pl::7")
pid, pname = app._drop_playlist_id(
    app.pl_tree2,
    app.pl_tree2.winfo_rootx() + (rowbox[0] if rowbox else 10),
    app.pl_tree2.winfo_rooty() + (rowbox[1] + 4 if rowbox else 4))
chk("拖到清单名字行取对应清单", pid == 7 and pname == "测试清单", f"{pid}/{pname}")

app._sync_playlist_song_titles()
chk("清单内歌曲题头带清单名",
    "[测试清单] 清单内的歌曲" in str(app.pl_songs2.master.cget("text")),
    str(app.pl_songs2.master.cget("text")))
app.pl_tree2.focus_set()
root.update()
time.sleep(0.1)
root.update()
app.lib_tree2.focus_set()
root.update()
time.sleep(0.1)
root.update()
chk("播放清单失焦后保持淡黄高亮",
    "warm" in app.pl_tree2.item("pl::7", "tags"),
    str(app.pl_tree2.item("pl::7", "tags")))

# 机器内歌曲连续拖入：新 ID 必须回传
app._worker = lambda fn, **kw: captured.append(fn)
app.cur_playlist = 7
app._pl_drag_ids = [101, 102]
root.winfo_containing = lambda x, y: app.pl_songs2
app.lib_tree = app.lib_tree2   # 模拟 _dispatch_tree_event 的临时指向
app.on_lib_drag_release(None)
fn = captured[-1]
events.clear()
evs = []
app.q.put = lambda item: evs.append(item)
fn(FakeDev())
chk("机器内歌曲写入后回传新清单 ID",
    any(e[0] == "playlist_retarget" and e[2] == 8 for e in evs), str(evs))
app.q.put = real_put

print("=== 4. 搜索与横向滚动联动 ===")
app.lib_search_var.set("Alpha")
app.tracks = [{"trackid": 101, "title": "Alpha", "length": 120},
              {"trackid": 102, "title": "Beta", "length": 100}]
app._render_lib()
app.lib_tree.selection_set("101")
app.action_find_lib()
chk("副套曲库同步定位", "101" in app.lib_tree2.selection(), str(app.lib_tree2.selection()))
chk("队列底部有横向滚动条", app.queue_hscroll.winfo_exists() == 1
    and app.queue_hscroll.winfo_ismapped() == 1)
app._meta_view.configure(width=60)
root.update()
app.tree.xview_moveto(0.5)
root.update()
time.sleep(0.1)
root.update()
chk("元数据随队列横向滚动", app._meta_shift["x"] < 0 or
    app._meta_inner.winfo_reqwidth() <= app._meta_view.winfo_width(),
    str(app._meta_shift))

print("=== 5. 播控顺序、GO、音量与进度气泡 ===")
app.stage_tracks = [app._new_stage_item(t) for t in app.tracks]
app.stage_tracks.append(app._new_stage_item({"trackid": 103, "title": "Gamma", "length": 90}))
app._render_stage()
ids0 = [t.get("trackid") for t in app.stage_tracks]
app._worker = lambda fn, **kw: None
app.play_mode_var.set("GO 模式（按 GO 逐曲）")
app.stage_index = 2
app.action_stage_go()          # v40：★GO 按钮=无参调用，播放选定后选定跳下一
chk("GO 不重排清单", [t.get("trackid") for t in app.stage_tracks] == ids0)
chk("# 恒为 1,2,3",
    [app.stage_tree.item(i, "values")[0] for i in app.stage_tree.get_children()] == ["1", "2", "3"],
    str([app.stage_tree.item(i, "values")[0] for i in app.stage_tree.get_children()]))
chk("GO 记录播放行", app._play_stage_index == 2)
chk("GO 底图跳到下一行", app.stage_index == 0, str(app.stage_index))
chk("GO 只装载当前曲", app._device_order == [2], str(app._device_order))
app.stage_tree.selection_set("1")
app._last_focus_tree = app.stage_tree
app.action_play_selected()
chk("软件播放也不重排清单", [t.get("trackid") for t in app.stage_tracks] == ids0)
app.action_stage_go(2)
app.play_state = "playing"
app._patch_stage_state()
chk("播放符号在真实播放行",
    [app.stage_tree.item(i, "values")[1] for i in app.stage_tree.get_children()]
    == ["", "", "▶"])
app._on_queue_end()
chk("GO 曲终停止", app.play_state == "stopped")
chk("软件播放按钮存在", hasattr(app, "play_soft_btn"))
prow = app._play_info_row()
chk("音量与总时长同行", inside(app.vol2_scale, prow))
chk("进度气泡已创建", hasattr(app, "_play_tip") and app._play_tip.winfo_exists() == 1)

root.destroy()
print(f"结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
