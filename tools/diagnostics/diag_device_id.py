"""真机验证设备序列号 read_id() 是否稳定、可作为缓存主键。

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
    print("open OK", flush=True)
    ids = []
    for i in range(3):
        t = time.monotonic()
        raw = d.read_id()
        ids.append(raw)
        print(f"read_id#{i + 1}: {raw.hex()}  len={len(raw)}  "
              f"{time.monotonic() - t:.4f}s", flush=True)
    print("STABLE:", len(set(ids)) == 1, flush=True)
    ping = d.ping()
    print("fw:", ping.get("fw"), "product:", ping.get("product"), flush=True)
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
