"""v46 真机端到端验证：首次完整读→缓存→快读→强制完整刷新。

通过 StudioApp 的 _SafePlaylistDevice 代理走真实设备，验证三个分支。
禁止管道截断；全程 try/finally 归还设备。
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for _p in (str(HERE), str(ROOT / "src" / "scripts"), str(ROOT / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import njb3_pde as pde
import njb3_studio as studio


class App:
    def __init__(self):
        self._force_full_library_read = False
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)
        print("  LOG:", msg, flush=True)


CACHE = HERE / "njb3_library_cache.json"
backup = CACHE.read_bytes() if CACHE.exists() else None
d = pde.NJB3Device(timeout=4000)
app = App()
try:
    d.open()
    print("open OK", flush=True)
    proxy = studio._SafePlaylistDevice(d, app)
    serial = studio.serial_key(proxy.read_id())
    print("serial:", serial, flush=True)

    print("\n--- 场景1：删除缓存后首次完整读 ---", flush=True)
    if CACHE.exists():
        CACHE.unlink()
    t = time.monotonic()
    t1 = proxy.list_tracks(extended=True)
    print(f"首读 {time.monotonic()-t:.2f}s / {len(t1)} 首", flush=True)
    cached = studio.load_library_cache().get("devices", {}).get(serial)
    print("缓存已建立:", bool(cached), "count:", cached and cached.get("count"), flush=True)
    ext_count = sum(1 for x in t1 if x.get("filename") or x.get("folder"))
    print("完整读带扩展字段的曲目数:", ext_count, flush=True)

    print("\n--- 场景2：同连接再读（快读 + 补字段）---", flush=True)
    # 清掉设备对象上的 basic 计数无法直接测，改为比较耗时与字段完整性
    t = time.monotonic()
    t2 = proxy.list_tracks(extended=True)
    dt = time.monotonic() - t
    print(f"快读 {dt:.2f}s / {len(t2)} 首", flush=True)
    ext2 = sum(1 for x in t2 if x.get("filename") or x.get("folder"))
    print("快读补回扩展字段的曲目数:", ext2, flush=True)
    print("快读判定:", "PASS" if dt < 10 and ext2 >= ext_count * 0.9 else "CHECK", flush=True)

    print("\n--- 场景3：强制完整刷新 ---", flush=True)
    app._force_full_library_read = True
    t = time.monotonic()
    t3 = proxy.list_tracks(extended=True)
    print(f"强制完整读 {time.monotonic()-t:.2f}s / {len(t3)} 首", flush=True)
    print("强制标志已清:", not app._force_full_library_read, flush=True)
    print("缓存已更新:", studio.load_library_cache()["devices"][serial]["count"], flush=True)
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
    if backup is not None:
        CACHE.write_bytes(backup)
        print("cache restored", flush=True)
    elif CACHE.exists():
        CACHE.unlink()
        print("cache removed", flush=True)
    time.sleep(0.3)
    try:
        v = pde.NJB3Device(timeout=2500)
        v.open()
        st = v.get_play_status()
        print("VERIFY OK state:", st.get("state"), flush=True)
        v.close()
    except Exception as ex:
        print("VERIFY FAIL:", type(ex).__name__, ex, flush=True)
