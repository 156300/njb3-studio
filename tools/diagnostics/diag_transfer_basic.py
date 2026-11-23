"""对照实验 3：list_tracks(extended=False) 精简字段读取耗时。

比较扩展字段（含文件名/文件夹等）与基础字段（无文件名/文件夹）的差异，
判断慢点是数据体积还是固件逐条检索。

安全：禁止管道截断；try/finally 归还设备。
"""
import sys
import time

import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

d = pde.NJB3Device(timeout=4000)
try:
    d.open()
    t = time.monotonic()
    tracks = d.list_tracks(extended=False)
    print(f"list_tracks(basic): {time.monotonic()-t:.3f}s  count={len(tracks)}",
          flush=True)
    if tracks:
        print("keys:", sorted(tracks[0].keys()), flush=True)
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
