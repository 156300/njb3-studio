#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v44 专项测试：
A 拖放——副套树(lib_tree2)发起拖动能取到 ID；拖进播控队列不需先选清单。
B 曲终——单一触发、无提前量、tick 1:1；模拟多首连播不重复切歌。
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
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


root = tk.Tk()
app = studio.StudioApp(root)
root.geometry("1400x820+0+0")
root.update()

# ============================================================ A. 拖放
print("=== A. 机器内歌曲拖放 ===")

# A1: 副套树选中两行，事件 widget=lib_tree2 → 拖起应取到这两个 ID
for tree in (app.lib_tree, app.lib_tree2):
    for iid in ("76001", "76002", "76003"):
        if not tree.exists(iid):
            tree.insert("", "end", iid=iid, values=(iid, "测试曲目"))
app.lib_tree2.selection_set("76001")
app.lib_tree2.selection_add("76002")


class Ev:
    def __init__(self, widget, y=5):
        self.widget = widget
        self.y = y
        self.x = 5


app.on_lib_drag_start(Ev(app.lib_tree2))
ids = getattr(app, "_pl_drag_ids", [])
chk("副套树拖动取到正确 ID", set(ids) == {76001, 76002}, str(ids))

# A2: 释放到播控队列目标，即使 cur_playlist=None 也应加入 stage
app.stage_tracks = []
app.cur_playlist = None
# 让 _chain_has 命中 stage_tree：用 stage_tree 自身作为 containing 结果
_orig_containing = app.root.winfo_containing
app.root.winfo_containing = lambda x, y: app.stage_tree
app._append_stage_ids = lambda ids2, src: app.stage_tracks.extend(
    [{"trackid": int(i)} for i in ids2])
chk("拖入目标命中播控队列", app._chain_has(app.stage_tree, app.stage_tree))
try:
    app.on_lib_drag_release(Ev(app.lib_tree2))
finally:
    app.root.winfo_containing = _orig_containing
chk("未选播放清单也能拖入播控队列",
    [t["trackid"] for t in app.stage_tracks] == [76001, 76002],
    str(app.stage_tracks))

# A3: 主套树拖动仍正常
app.lib_tree2.selection_remove(*app.lib_tree2.selection())
app.lib_tree.selection_set("76003")
app.on_lib_drag_start(Ev(app.lib_tree))
chk("主套树拖动取到正确 ID", getattr(app, "_pl_drag_ids", []) == [76003],
    str(getattr(app, "_pl_drag_ids", None)))

# ============================================================ B. 曲终单一触发
print("=== B. 曲终调度 ===")
app.stage_tracks = [
    {"trackid": 500 + i, "title": f"T{i}", "length": 3} for i in range(4)]
app.play_mode_var.set("顺序播放（放完停止）")
app.connected = True
app.worker_busy = False
app._played_marks = set()

play_log = []


class Dev:
    def clear_play_queue(self):
        play_log.append("clear")
        return True

    def play_track(self, tid):
        play_log.append(("play", tid))
        return True

    def play_control(self, cmd):
        play_log.append(("stop", cmd))
        return True


app._worker = lambda fn, *a, **kw: fn(Dev())

# 播第 0 首
app.action_stage_go(0)
chk("起播：清空队列并只播第一首",
    play_log == ["clear", ("play", 500)], str(play_log))

# 曲末高频/提前量不存在
app.play_state = "playing"
app.play_duration_sec = 3
app.play_elapsed_sec = 2.7
sync_calls = []
app._request_play_sync = lambda force=False: sync_calls.append(force)
pre_calls = []
_orig_adv = app._auto_advance_on_end
app._auto_advance_on_end = lambda: pre_calls.append(1)
app._tick_tail_align()
chk("曲末无提前量触发（不会双切）", not pre_calls, str(pre_calls))
chk("曲末无高频同步（不打架）", not sync_calls, str(sync_calls))
app._auto_advance_on_end = _orig_adv
app._request_play_sync = lambda force=False: None

# 模拟 tick 一秒一秒走到 total，验证：到头那一刻才切一次，下一首继续
app.play_state = "playing"
app.play_elapsed_sec = 0.0
app.play_duration_sec = 3
app.play_last_sync = time.monotonic()
app._play_stage_index = 0
app.stage_index = 1
app._tick_playback()   # 第 1 秒
app._tick_playback()   # 第 2 秒
chk("逐秒推进 1:1（2 秒时到 2）", abs(app.play_elapsed_sec - 2.0) < 0.001,
    str(app.play_elapsed_sec))
app._tick_playback()   # 第 3 秒 → 到 total，触发切歌并把进度归零
chk("到 total 由 ended 触发一次切歌（播第 1 首）",
    any(c == ("play", 501) for c in play_log), str(play_log))

# 统计第1首被提交的次数：应为1（无重复切歌）
cnt = sum(1 for c in play_log if c == ("play", 501))
chk("同一曲终只提交一次下一首（不重复/不乱跳）", cnt == 1, f"count={cnt}")

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
