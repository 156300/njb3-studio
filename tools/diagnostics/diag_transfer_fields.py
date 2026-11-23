"""对照实验 4：逐字段定位 extended 曲库读取为何慢 17 倍。

extended 比 basic 多请求 F_FOLDER(0x000D) 与 F_FNAME(0x0007)。
分别单独请求，比较耗时，找出拖慢设备的具体字段。

安全：禁止管道截断；try/finally 归还设备。
"""
import inspect
import sys
import textwrap
import time

import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

src = textwrap.dedent(inspect.getsource(pde.NJB3Device.list_tracks))


def make(label, remove):
    s = src.replace("def list_tracks(", f"def list_{label}(", 1)
    for old, new in remove:
        assert old in s, old
        s = s.replace(old, new, 1)
    ns = dict(vars(pde))
    exec(s, ns)
    return ns[f"list_{label}"]


fname_only = make("fname", [("ff fe 00 18", "ff fe 00 16"),
                            ("00 0d 00 07", "00 07")])
folder_only = make("folder", [("ff fe 00 18", "ff fe 00 16"),
                              ("00 0d 00 07", "00 0d")])

d = pde.NJB3Device(timeout=4000)
try:
    d.open()
    for label, fn in (("basic", lambda: d.list_tracks(extended=False)),
                      ("fname_only", lambda: fname_only(d, extended=True)),
                      ("folder_only", lambda: folder_only(d, extended=True))):
        t = time.monotonic()
        tracks = fn()
        print(f"{label:12s} {time.monotonic()-t:7.3f}s  count={len(tracks)}"
              f"  keys={sorted(tracks[0].keys()) if tracks else []}", flush=True)
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
