"""对照实验 2：元数据 bulk read 每次只取 4096 字节。

若曲库读取显著加速，说明 ~2s/批 是 libusb 等待“大包剩余字节”的延迟；
若仍 ~57s，说明是固件准备每批数据的固有耗时。

安全：禁止管道截断；try/finally 归还设备。
"""
import sys
import time

import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

BIG = 0x100000 + 0x100
SMALL = 4096
n = [0]
sec = [0.0]
orig_read = pde.NJB3Device._bulk_read


def tuned_read(self, size=4096):
    if size == BIG:
        n[0] += 1
        t = time.monotonic()
        try:
            return orig_read(self, SMALL)
        finally:
            sec[0] += time.monotonic() - t
    return orig_read(self, size)


pde.NJB3Device._bulk_read = tuned_read

d = pde.NJB3Device(timeout=4000)
try:
    d.open()
    t = time.monotonic()
    tracks = d.list_tracks(extended=True)
    print(f"list_tracks: {time.monotonic()-t:.3f}s  count={len(tracks)}"
          f"  reads={n[0]}  read_sec={sec[0]:.3f}", flush=True)
except Exception as ex:
    print("DIAG FAIL:", type(ex).__name__, ex, flush=True)
finally:
    for label, fn in (
        ("PLAY_STOP", lambda: d.play_control(pde.NJB3Device.PLAY_STOP)),
        ("recover", d.recover),
        ("close", d.close),
    ):
        try:
            fn()
            print(f"cleanup {label}: OK", flush=True)
        except Exception as ex:
            print(f"cleanup {label}: {type(ex).__name__} {ex}", flush=True)
    time.sleep(0.3)
    try:
        v = pde.NJB3Device(timeout=2500)
        v.open()
        st = v.get_play_status()
        print("VERIFY OK state:", st.get("state"), flush=True)
        v.close()
    except Exception as ex:
        print("VERIFY FAIL:", type(ex).__name__, ex, flush=True)
