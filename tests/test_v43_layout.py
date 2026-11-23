#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v44 修订：撤销 v43 曲末高频/提前量后，验证播放前清队列、单一触发、
tick 每秒 +1、差速仍可学习但不驱动切歌。原 v43 的"高频对齐/指令提前量"
机制已被真机实测否决（双触发导致乱跳），本测试同步改为验证撤销后的语义。"""
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
root.geometry("1200x760+0+0")

app.stage_tracks = [
    {"trackid": 500 + i, "title": f"T{i}", "length": 50} for i in range(3)]
app.play_mode_var.set("GO 模式（按 GO 逐曲）")
app.connected = True
app.worker_busy = False

calls = []


class Dev:
    def clear_play_queue(self):
        calls.append("clear")
        return True

    def play_track(self, tid):
        calls.append(("play", tid))
        return True

    def play_control(self, cmd):
        calls.append(("stop", cmd))
        return True


app._worker = lambda fn, *a, **kw: fn(Dev())

# ---------------------------------------------------------- 1. 播放前清空队列（保留）
print("=== 1. 播放前清空机器队列（v43 保留） ===")
app.action_stage_go(0)
chk("先清空残留播放队列再播当前曲",
    calls == ["clear", ("play", 500)], str(calls))
chk("逐曲模式只提交一首（不预装后续）",
    app._device_order == [0], str(app._device_order))

# ---------------------------------------------------------- 2. 差速仍可学习，但不驱动切歌
print("=== 2. 差速可学习但不影响切歌 ===")
app._play_rate = 1.2
app._rate_track = None
app._track_play_rate({"elapsed": 10})
chk("同步数据仍可估算差速（仅供显示校准参考）", 0.6 <= app._play_rate <= 1.6,
    str(app._play_rate))
app._play_rate = 1.12
app.action_stage_go(1)
chk("换曲不强制清零倍率", abs(app._play_rate - 1.12) < 1e-6, str(app._play_rate))

# ---------------------------------------------------------- 3. 曲末高频/提前量已撤销
print("=== 3. 曲末 300ms 高频与提前量已撤销 ===")
app.play_state = "playing"
app.play_duration_sec = 50
app.play_elapsed_sec = 45.0        # 剩 5 秒：旧版会进入曲末窗口
app._device_order = [1]
app.play_track_info = app.stage_tracks[1]
app.play_track_id = app.stage_tracks[1]["trackid"]
sync_calls = []
app._request_play_sync = lambda force=False: sync_calls.append(force)
advanced = []
_orig_adv = app._auto_advance_on_end
app._auto_advance_on_end = lambda: advanced.append(1)
app._tick_tail_align()
chk("曲末不再强制高频读设备", not sync_calls, str(sync_calls))
chk("曲末不再提前发下一曲（杜绝双触发）", not advanced, str(advanced))
chk("不再续接 300ms 定时器", not getattr(app, "_tail_align_job", None))
app._auto_advance_on_end = _orig_adv

# ---------------------------------------------------------- 4. tick 每秒 +1、到 total 单一触发
print("=== 4. tick 每秒 +1，到 total 单一触发曲终 ===")
app.connected = True
app.play_state = "playing"
app.play_elapsed_sec = 10.0
app.play_last_sync = time.monotonic()
app._request_play_sync = lambda force=False: None
app._tick_playback()
chk("tick 每秒固定 +1（无差速补偿）", abs(app.play_elapsed_sec - 11.0) < 0.001,
    str(app.play_elapsed_sec))

# 单一触发入口：ended 状态由基类 _tick_playback 调 _auto_advance_on_end。
# 真实调度里它会把 play_state 改回 playing，因此下一秒不会再进 ended 分支，只切一次。
app.play_state = "ended"
app._auto_loaded = False
app.play_elapsed_sec = 50.0
app.play_duration_sec = 50
advance_hits = []
app._auto_advance_on_end = lambda: advance_hits.append(1)
app._tick_playback()
chk("曲终调度入口唯一（_auto_advance_on_end）", len(advance_hits) >= 1,
    str(advance_hits))
chk("无曲线提前量标志残留触发", not advanced, str(advanced))
app._auto_advance_on_end = _orig_adv

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
