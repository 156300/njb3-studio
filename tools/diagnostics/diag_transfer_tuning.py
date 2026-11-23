"""对照实验：把元数据 bulk read 的请求长度从 1MB 收窄到 64KB+256B。

若曲库读取从 ~57s 降到几秒且曲目数一致，则慢点是 libusb 空等大包；
若仍然 ~57s，则慢点在设备固件逐批准备数据，与本机读取长度无关。

安全：禁止管道截断；try/finally 归还设备。
"""
import sys
import time

import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

BIG = 0x100000 + 0x100
SMALL = 0x10000 + 0x100
BIG_READS = [0]
BIG_SEC = [0.0]
orig_read = pde.NJB3Device._bulk_read


def tuned_read(self, size=4096):
    if size == BIG:
        BIG_READS[0] += 1
        t = time.monotonic()
        try:
            return orig_read(self, SMALL)
        finally:
            BIG_SEC[0] += time.monotonic() - t
    return orig_read(self, size)


pde.NJB3Device._bulk_read = tuned_read

d = pde.NJB3Device(timeout=4000)
try:
    d.open()
    print("open OK", flush=True)
    t = time.monotonic()
    tracks = d.list_tracks(extended=True)
    print(f"list_tracks: {time.monotonic()-t:.3f}s  count={len(tracks)}"
          f"  capped_reads={BIG_READS[0]}  capped_sec={BIG_SEC[0]:.3f}", flush=True)
    t = time.monotonic()
    pls = d.list_playlists()
    print(f"list_playlists: {time.monotonic()-t:.3f}s  count={len(pls)}", flush=True)
    for pl in pls:
        pid = pl.get("playlistid")
        if pid is None:
            continue
        t = time.monotonic()
        m = d.get_playlist_tracks(pid)
        print(f"  members#{pid} {pl.get('name','')}: {time.monotonic()-t:.3f}s"
              f"  count={len(m)}", flush=True)
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
