"""安全联机探测：全程 try/finally，退出前 STOP + recover + close。
禁止用 | head 等管道截断本脚本输出（会导致 SIGTERM 强杀、设备卡死）。
"""
import sys, time
import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

d = pde.NJB3Device(timeout=3000)
opened = False
try:
    d.open()
    opened = True
    print("open OK", flush=True)
    t0 = time.monotonic()
    st = d.get_play_status()
    print(f"play_status: {st}  ({time.monotonic()-t0:.3f}s)", flush=True)
    t0 = time.monotonic()
    e = d.get_elapsed_time()
    q = d.get_current_track()
    print(f"elapsed={e} qpos={q}  ({time.monotonic()-t0:.3f}s)", flush=True)
except Exception as ex:
    print("PROBE FAIL:", type(ex).__name__, ex, flush=True)
finally:
    # 无论成败，先让机器退出播放态，再归还设备占用。
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
    # 复核：开一条新连接读一次状态，确认设备已正常归还。
    try:
        v = pde.NJB3Device(timeout=2500)
        v.open()
        s = v.get_play_status()
        print("VERIFY OK state:", s.get("state"), flush=True)
        v.close()
    except Exception as ex:
        print("VERIFY FAIL:", type(ex).__name__, ex, flush=True)
