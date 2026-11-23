#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v41 专项测试：
1) 仅“删除选中”弹窗带 n/N 计数与确定进度条，其他弹窗不变；
2) 浅色主题下传输页元数据画布与 PanedWindow 分隔条不再留黑条；
3) 播控页“软件播放”播放当前后选定跳下一首，右键播放选中曲目不跳；
4) 设备同步不再把逐曲播放的总时长切成不同曲目，差速倍率可学习；
5) 播放清单选中只查设备一次，显式刷新才重查。
"""
import sys
import time
from pathlib import Path
from tkinter import ttk

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
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


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.update()
time.sleep(0.2)
root.update()

# ---------------------------------------------------------------- 1. 删除进度
print("=== 1. 删除选中弹窗 n/N ===")
app.tracks = [{"trackid": 101, "title": "A", "length": 60},
              {"trackid": 102, "title": "B", "length": 60}]
app._render_lib()
app.lib_tree2.selection_set("101")
app.lib_tree2.selection_add("102")
events = []
_orig_put = app.q.put
app.q.put = events.append
_orig_yes = studio.messagebox.askyesno
studio.messagebox.askyesno = lambda *a, **k: True
orig_worker = app._worker
labels = []


class _Cap:
    def delete_item(self, tid):
        pass


def _run_delete(fn, *a, **kw):
    labels.append(str(kw.get("label", "")))
    return fn(_Cap())


app._worker = _run_delete
app._call_with_tree("lib_tree", app.lib_tree2, app.action_delete_tracks)
stats = [e[1] for e in events if e[0] == "progress_stat"]
chk("删除发 0/N→N/N 计数", stats == ["0/2", "1/2", "2/2"], str(stats))
chk("删除弹窗标题含首数",
    any("删除" in lab and "2" in lab for lab in labels), str(labels))
chk("删除期间标记计数模式", app._delete_progress_active)
app._create_progress_win()
chk("删除进度条为确定模式",
    app.progress_pb is not None and str(app.progress_pb.cget("mode")) == "determinate",
    str(app.progress_pb))
chk("进度条最大值=删除数", int(app.progress_pb.cget("maximum")) == 2)
app._handle(("progress_stat", "2/2"))
chk("右上角显示 2/2", app.progress_stat_var.get() == "2/2")
chk("进度条值随事件推进", float(app.progress_pb.cget("value")) == 2.0)
app._hide_progress()
chk("关闭后退出删除计数模式", not app._delete_progress_active)
events.clear()
app._show_progress("正在刷新曲库 ...")
chk("其他任务不设计数模式", not app._delete_progress_active)
app._hide_progress()
studio.messagebox.askyesno = _orig_yes
app._worker = orig_worker
app.q.put = _orig_put

# ---------------------------------------------------------------- 2. 黑条
print("=== 2. 浅色主题无黑条 ===")
app.apply_studio_theme("暖米", save=False)
chk("传输分隔条取当前主题 sash",
    str(app.transfer_paned.cget("bg")).lower() == app.pal["sash"].lower(),
    f"{app.transfer_paned.cget('bg')} vs {app.pal['sash']}")
try:
    sw = int(app.transfer_paned.cget("sashwidth"))
except Exception:
    sw = -1
chk("传输分隔条收窄为 4px", sw == 4, str(sw))
chk("元数据画布跟随面板色",
    str(app._meta_view.cget("bg")).lower() == app.pal["panel"].lower(),
    f"{app._meta_view.cget('bg')} vs {app.pal['panel']}")
app.apply_studio_theme("深空石墨", save=False)

# ---------------------------------------------------------------- 3. 软件播放跳选
print("=== 3. 播控页软件播放跳选 ===")
app.stage_tracks = [{"trackid": 300 + i, "title": f"T{i}", "length": 100}
                    for i in range(4)]
app.play_mode_var.set("GO 模式（按 GO 逐曲）")
app._worker = lambda fn, *a, **kw: fn(type("D", (), {
    "play_track": lambda s, t: True,
    "play_control": lambda s, c: True,
    "seek": lambda s, ms: True})())
_orig_anywhere = app._selected_track_anywhere
app._selected_track_anywhere = lambda: (app.stage_tracks[1], "stage")
app.stage_index = 1
app.action_play_selected(advance_selection=True)
chk("播控软件播放当前并跳下一",
    app._play_stage_index == 1 and app.stage_index == 2,
    f"play={app._play_stage_index} sel={app.stage_index}")
app._selected_track_anywhere = lambda: (app.stage_tracks[3], "stage")
app.stage_index = 1
app.action_play_selected()
chk("右键播放选中曲目仍不跳",
    app._play_stage_index == 3 and app.stage_index == 3,
    f"play={app._play_stage_index} sel={app.stage_index}")
app._selected_track_anywhere = _orig_anywhere
chk("换曲后差速倍率重置", app._play_rate == 1.0, str(app._play_rate))

# ---------------------------------------------------------------- 4. 时长锁定与差速
print("=== 4. 同步时长锁定与差速补偿 ===")
app.stage_tracks = [{"trackid": 400 + i, "title": f"X{i}", "length": (10, 20, 40, 80)[i]}
                    for i in range(4)]
app._play_stage_index = 2
app._device_order = [2]
app.play_track_info = app.stage_tracks[2]
app.play_track_id = 402
app.play_queue_pos = 0
app.play_state = "playing"
app._duration_synced_tid = None
app.play_duration_var.set("00:10")
app.play_duration_sec = 10
app._obs_last = None
app._was_playing = True
app._boundary_handled = True
app.q.put = events.append
app._handle(("play_sync",
             {"state": "playing", "playing": True, "elapsed": 5, "queue_pos": 0},
             {"advanced": False}))
chk("设备陈旧队列位置不切曲目", app._play_stage_index == 2
    and app.play_track_id == 402, f"{app._play_stage_index}/{app.play_track_id}")
chk("总时长锁定当前曲目 00:40", app.play_duration_var.get() == "00:40",
    app.play_duration_var.get())
length_logs = [e for e in events if e[0] == "log" and "总时长" in str(e[1])]
chk("同一曲目只写一次正确时长",
    len(length_logs) == 1 and "00:40" in str(length_logs[0][1]),
    str(length_logs))
app.q.put = _orig_put

app.connected = True
app.play_state = "playing"
app._rate_track = app._play_stage_index
app._rate_last_elapsed = 2
app._rate_last_t = time.monotonic() - 1.5
app._play_rate = 1.0
app._track_play_rate({"elapsed": 3})   # 1.5 秒 PC 时间设备只走 1 秒 → 偏慢
rate = app._play_rate
chk("按同步数据估算差速", 0.6 < rate < 1.0, str(rate))
app.play_elapsed_sec = 10.0
app.play_last_sync = time.monotonic()
app._tick_playback()
# v44：真机实测设备时钟与 PC 1:1，tick 不再叠加差速补偿，每秒固定 +1。
chk("每秒 tick 固定 +1（不叠加差速）",
    abs(app.play_elapsed_sec - 11.0) < 0.001,
    f"{app.play_elapsed_sec} vs 11.0")
app.connected = False

# ---------------------------------------------------------------- 5. 清单只查一次
print("=== 5. 清单选中不轮询 ===")
app.playlists = [{"playlistid": 7, "name": "T"}]
app.pl_members = {}
app._pl_checked_members.clear()
app.cur_playlist = None
app._render_playlists()
app.pl_tree.selection_set("pl::7")
calls = []
app._worker = lambda fn, *a, **kw: calls.append(fn)
app.on_playlist_select(None)
chk("首次选中读设备一次", len(calls) == 1, str(len(calls)))
app.on_playlist_select(None)
app._render_playlists()
chk("重复选中/重绘不再读取", len(calls) == 1, str(len(calls)))
app.action_refresh_pl_members()
chk("右键刷新成员强制重读", len(calls) == 2, str(len(calls)))
app._render_playlists()
chk("重读后的重绘也不轮询", len(calls) == 2, str(len(calls)))

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
