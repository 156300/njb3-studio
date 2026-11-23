#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探测：连接设备，找最短曲目，打印当前播放状态。不播放、不写任何数据。"""
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


def main():
    print("== 探测设备 ==")
    d = pde.NJB3Device()
    try:
        d.open()
        print("open OK")
    except pde.NeedWinUSB as e:
        print("NeedWinUSB, 尝试 recover:", e)
        try:
            d.recover()
            d = pde.NJB3Device()
            d.open()
            print("recover + open OK")
        except Exception as e2:
            print("recover 失败:", type(e2).__name__, e2)
            return
    except Exception as e:
        print("open 失败:", type(e).__name__, e)
        return

    try:
        info = d.ping()
        print("ping:", info)
    except Exception as e:
        print("ping 失败:", type(e).__name__, e)

    print("\n== 读取曲库（可能几秒）==")
    t0 = time.monotonic()
    try:
        tracks = d.list_tracks(extended=True)
        print(f"曲库 {len(tracks)} 首，耗时 {time.monotonic()-t0:.1f}s")
    except usb.core.USBError as e:
        print("读曲库 USB 超时:", e)
        d.close()
        return
    except Exception as e:
        print("读曲库失败:", type(e).__name__, e)
        d.close()
        return

    # 找最短且有时长的曲目（用 length 字段）
    cand = [t for t in tracks if isinstance(t.get("length"), (int, float))
            and t["length"] > 0]
    cand.sort(key=lambda t: t["length"])
    print("\n== 最短 12 首 ==")
    for t in cand[:12]:
        print(f"  {t['length']:>4}s  id={t.get('trackid')}  {t.get('title','?')[:40]}")

    print("\n== 当前播放状态 ==")
    try:
        obs = d.observe_playback()
        print("  ", obs)
    except Exception as e:
        print("  观测失败:", type(e).__name__, e)

    d.close()
    print("\n已释放设备。")


if __name__ == "__main__":
    main()
