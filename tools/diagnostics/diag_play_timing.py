#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机时序实测：1) 三条只读命令耗时；2) 播一首短曲，每秒采样 elapsed。"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for _p in (str(HERE), str(ROOT / "src" / "scripts"), str(ROOT / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import usb.core
import njb3_pde as pde

TRACK = 76154     # 43s 最短曲
DUR = 43


def timed(fn, label):
    t0 = time.monotonic()
    try:
        v = fn()
        dt = time.monotonic() - t0
        print(f"  {label:<14} {dt*1000:6.1f}ms  -> {v}")
        return v, dt
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"  {label:<14} {dt*1000:6.1f}ms  !! {type(e).__name__}: {e}")
        return None, dt


def main():
    d = pde.NJB3Device()
    d.open()
    print("open OK")

    print("\n== 1. 三条只读命令各测 5 次耗时 ==")
    for name, fn in (("get_elapsed", d.get_elapsed_time),
                     ("get_current_track", d.get_current_track),
                     ("get_play_status", d.get_play_status)):
        print(f" {name}:")
        for _ in range(5):
            timed(fn, name)

    print("\n== 2. 播放 43s 短曲，每秒采样 ==")
    print("  seek 到 0 后 play_track")
    ok, _ = timed(lambda: d.play_track(TRACK), "play_track")
    print("  play ok:", ok)
    t_start = time.monotonic()
    samples = []
    last_wall = t_start
    # 采样 ~48 秒，覆盖曲终；每轮只读 elapsed + state
    for i in range(50):
        wall = time.monotonic() - t_start
        e, _ = timed(d.get_elapsed_time, "elapsed")
        try:
            st = d.get_play_status()
            state = st["state"]
        except Exception:
            state = None
        row = (wall, e, state)
        samples.append(row)
        print(f"  wall={wall:6.2f}s elapsed={str(e):>5} state={state}")
        if i > 5 and state == 1:      # PLAY_STOP
            print("  -> 设备已停止，提前结束采样")
            break
        # 对齐到整秒
        sleep = 1.0 - (time.monotonic() - last_wall) % 1.0
        time.sleep(max(0, sleep))
        last_wall = time.monotonic()

    print("\n== 汇总 ==")
    print("  wall(sec) | elapsed | state")
    for w, e, s in samples:
        print(f"  {w:8.2f} | {str(e):>5} | {s}")

    try:
        d.play_control(1)
    except Exception:
        pass
    d.close()
    print("\n已停止并释放设备。")


if __name__ == "__main__":
    main()
