#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v42 专项测试：
1) 机器内歌曲/播放清单右键菜单命令绑定事件来源树，不再读错框体；
2) 拖放来源树被记下，搜索按实际树定位；
3) 空/损坏清单读取超时按空清单跳过，不拖垮连接；
4) 删除曲目前先重建受影响播放清单，且只重定当前选中清单；
5) 删除后本地曲库、清单缓存、播放队列同步清除陈旧 ID。
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
import usb.core
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


class Ev:
    def __init__(self, widget, y=8):
        self.widget = widget
        self.y = y
        self.x_root = 10
        self.y_root = 10


def row_y(tree, iid):
    try:
        box = tree.bbox(iid)
        if box:
            return box[1] + box[3] // 2
    except Exception:
        pass
    return 8


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.update()
time.sleep(0.2)
root.update()

app.tracks = [{"trackid": 101, "title": "A", "length": 60},
              {"trackid": 102, "title": "B", "length": 60},
              {"trackid": 103, "title": "目标曲", "length": 60}]
app.playlists = [{"playlistid": 7, "name": "测试清单"},
                 {"playlistid": 8, "name": "空清单"}]
app.pl_members = {7: [101, 102], 8: []}
app.cur_playlist = 7
app._render_lib()
app._render_playlists()
app._render_pl_songs()

# ------------------------------------------------------------ 1. 曲库右键菜单
print("=== 1. 机器内歌曲右键命令归属 ===")
app.lib_tree2.selection_set("101")
app.lib_tree2.selection_add("102")
captured = []
_orig_add = tk.Menu.add_command
_orig_popup = tk.Menu.tk_popup


def _rec_add(self, **kw):
    captured.append((kw.get("label"), kw.get("command")))
    return _orig_add(self, **kw)


tk.Menu.add_command = _rec_add
tk.Menu.tk_popup = lambda self, x, y: None
try:
    app.on_lib_context(Ev(app.lib_tree2, row_y(app.lib_tree2, "101")))
finally:
    tk.Menu.add_command = _orig_add
    tk.Menu.tk_popup = _orig_popup
cmd = next((c for lab, c in captured if lab and lab.startswith("删除")), None)
chk("右键菜单含删除命令", cmd is not None, str([x[0] for x in captured]))
seen = {}
_orig_delete = app.action_delete_tracks
app.action_delete_tracks = lambda: seen.setdefault("tree", app.lib_tree)
try:
    cmd()
finally:
    app.action_delete_tracks = _orig_delete
chk("右键删除读到副套机器内歌曲树", seen.get("tree") is app.lib_tree2,
    str(seen.get("tree")))
chk("菜单执行后主套引用已还原", app.lib_tree is not app.lib_tree2)

# ------------------------------------------------------------ 2. 清单右键菜单
print("=== 2. 播放清单右键命令归属 ===")
captured.clear()
app.pl_tree2.selection_set("pl::7")
seen2 = {}
_orig_refresh = app.action_refresh_pl_members
# 菜单在生成时就捕获回调，因此必须在 on_playlist_context 之前打桩。
app.action_refresh_pl_members = lambda: seen2.setdefault("tree", app.pl_tree)
tk.Menu.add_command = _rec_add
tk.Menu.tk_popup = lambda self, x, y: None
try:
    app.on_playlist_context(Ev(app.pl_tree2, row_y(app.pl_tree2, "pl::7")))
    cmd2 = next((c for lab, c in captured if lab == "刷新成员"), None)
    chk("清单右键含刷新成员", cmd2 is not None, str([x[0] for x in captured]))
    if cmd2:
        cmd2()
finally:
    tk.Menu.add_command = _orig_add
    tk.Menu.tk_popup = _orig_popup
    app.action_refresh_pl_members = _orig_refresh
chk("右键刷新读到副套播放清单树", seen2.get("tree") is app.pl_tree2,
    str(seen2.get("tree")))

# ------------------------------------------------------------ 3. 拖放与搜索
print("=== 3. 拖放来源与搜索来源 ===")
app.lib_tree2.selection_set("101")
app._dispatch_tree_event(Ev(app.lib_tree2, row_y(app.lib_tree2, "101")),
                         app.lib_tree2, app.on_lib_drag_start)
chk("拖放记录副套来源树", getattr(app, "_pl_drag_src", None) is app.lib_tree2)
app.lib_search_var.set("目标曲")
app.lib_tree.selection_remove(*app.lib_tree.selection())
app.action_find_lib(app.lib_tree2)
chk("按副套树搜索命中目标行",
    "103" in app.lib_tree2.selection() and "103" in app.lib_tree.selection(),
    f"{app.lib_tree2.selection()} / {app.lib_tree.selection()}")

# ------------------------------------------------------------ 4. 空清单兜底
print("=== 4. 空清单读取超时兜底 ===")


class _TimeoutDev:
    def get_playlist_tracks(self, pid):
        raise usb.core.USBError("[Errno 10060] Operation timed out")

    def ping(self):
        return "alive"


proxy = studio._SafePlaylistDevice(_TimeoutDev(), app)
chk("空清单超时返回空列表", proxy.get_playlist_tracks(8) == [])
chk("代理其它方法正常透传", proxy.ping() == "alive")

# ------------------------------------------------------------ 5. 删除前重建清单
print("=== 5. 删除曲目前先重建受影响清单 ===")
app.tracks = [{"trackid": 101, "title": "A", "length": 60},
              {"trackid": 102, "title": "B", "length": 60},
              {"trackid": 103, "title": "C", "length": 60}]
app.playlists = [{"playlistid": 7, "name": "测试清单"},
                 {"playlistid": 8, "name": "另一清单"}]
app.pl_members = {7: [101, 102], 8: [101], 9: []}
app.cur_playlist = 7
app.stage_tracks = [{"trackid": 101}, {"trackid": 103}]
app._render_lib()
app._render_playlists()
app.lib_tree.selection_remove(*app.lib_tree.selection())
app.lib_tree2.selection_set("101")
app.lib_tree2.selection_add("102")


class _DelDev:
    def __init__(self):
        self.calls = []

    def replace_playlist(self, pid, name, ids):
        self.calls.append(("replace", pid, name, list(ids)))
        return pid + 1000

    def list_playlists(self):
        self.calls.append(("list",))
        return list(app.playlists)

    def delete_item(self, tid):
        self.calls.append(("delete", tid))


dev = _DelDev()
events = []
_orig_put = app.q.put
_orig_yes = studio.messagebox.askyesno
_orig_worker = app._worker
app.q.put = events.append
studio.messagebox.askyesno = lambda *a, **k: True
app._worker = lambda fn, *a, **kw: fn(dev)
try:
    app._call_with_tree("lib_tree", app.lib_tree2, app.action_delete_tracks)
finally:
    app.q.put = _orig_put
    studio.messagebox.askyesno = _orig_yes
    app._worker = _orig_worker

reps = [c for c in dev.calls if c[0] == "replace"]
dels = [c for c in dev.calls if c[0] == "delete"]
chk("两个受影响清单都先重建", len(reps) == 2
    and {c[1] for c in reps} == {7, 8}, str(reps))
chk("被删光成员的清单也重建为空",
    all(c[3] == [] for c in reps), str(reps))
chk("重建全部发生在删曲目之前",
    reps and dels and dev.calls.index(reps[-1]) < dev.calls.index(dels[0]),
    str(dev.calls))
chk("删除事件带 lib 与 stage_tracks",
    any(e[0] == "lib" for e in events)
    and any(e[0] == "stage_tracks" for e in events), str(events))
retargets = [e for e in events if e[0] == "playlist_retarget"]
chk("只重定当前选中清单", [e[1] for e in retargets] == [7], str(retargets))

# ------------------------------------------------------------ 6. 重定不选错
print("=== 6. 清单 ID 重定不抢选中 ===")
app.cur_playlist = 8
app._handle(("playlist_retarget", 7, 1007))
chk("非当前清单重建不改选中", app.cur_playlist == 8, str(app.cur_playlist))
app.cur_playlist = 7
app._handle(("playlist_retarget", 7, 1007))
chk("当前清单重建才改选中", app.cur_playlist == 1007, str(app.cur_playlist))

print(f"\n结果: {ok} OK / {bad} FAIL")
root.destroy()
sys.exit(1 if bad else 0)
