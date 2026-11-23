"""只读真机诊断：连接 → 设备信息 → 曲库 → 播放清单 → 清单成员 → 播放队列。

目的：定位“官方 CMS 几秒导入、我们软件很慢”到底卡在哪一步。
方法：包裹底层 bulk 读/写与 ctrl 传输，逐次记录耗时与字节数。

安全要求（务必遵守）：
  * 禁止用 | head 等管道截断本脚本输出，否则进程被强杀、设备停在同步态。
  * 全程 try/finally，退出前 STOP → recover → close，并复核设备已归还。
"""
import json
import sys
import time

import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

OUT_JSON = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "diag_transfer_timeline.json")

records = []
_stage = ["init"]
_read_count = [0]
_read_bytes = [0]


def _note(kind, sec, **kw):
    records.append({"kind": kind, "stage": _stage[0], "sec": round(sec, 4), **kw})


def _wrap(cls, name, kind):
    orig = getattr(cls, name)

    def fn(self, *a, **kw):
        t = time.monotonic()
        result = None
        try:
            result = orig(self, *a, **kw)
            return result
        finally:
            dt = time.monotonic() - t
            if kind == "read":
                _read_count[0] += 1
                try:
                    _read_bytes[0] += len(result or b"")
                except Exception:
                    pass
            _note(kind, dt, size=a[0] if a and kind in ("read", "write") else None,
                  bytes=len(result or b"") if kind == "read" else None,
                  args=repr(a)[:120])
    setattr(cls, name, fn)


_wrap(pde.NJB3Device, "_bulk_read", "read")
_wrap(pde.NJB3Device, "_bulk_write", "write")
_wrap(pde.NJB3Device, "_ctrl", "ctrl")


def stage(name, fn):
    _stage[0] = name
    t = time.monotonic()
    r0, w0 = _read_count[0], _read_bytes[0]
    try:
        value = fn()
        err = None
    except Exception as ex:
        value = None
        err = f"{type(ex).__name__}: {ex}"
    dt = time.monotonic() - t
    summary = {
        "stage": name,
        "sec": round(dt, 3),
        "bulk_reads": _read_count[0] - r0,
        "error": err,
    }
    if isinstance(value, (list, tuple, dict)):
        summary["count"] = len(value)
    print(f"[{name}] {dt:7.3f}s  reads={_read_count[0]-r0:4d}"
          f"  count={summary.get('count', '-')}" + (f"  ERR={err}" if err else ""),
          flush=True)
    return value, summary


d = pde.NJB3Device(timeout=4000)
summaries = []
_opened = False
playlists = []
try:
    t = time.monotonic()
    d.open()
    _opened = True
    print(f"[open] {time.monotonic()-t:.3f}s", flush=True)

    _, s = stage("ping", d.ping)
    summaries.append(s)
    _, s = stage("disk_usage", d.get_disk_usage)
    summaries.append(s)
    _, s = stage("battery", d.get_battery)
    summaries.append(s)
    _, s = stage("volume", d.get_volume)
    summaries.append(s)
    _, s = stage("eax", d.get_eax_master)
    summaries.append(s)

    tracks, s = stage("list_tracks(extended)", lambda: d.list_tracks(extended=True))
    summaries.append(s)
    if tracks:
        sample = {k: tracks[0].get(k) for k in ("trackid", "title", "artist", "length")}
        print("  first track:", sample, flush=True)

    playlists, s = stage("list_playlists", d.list_playlists)
    summaries.append(s)
    for pl in playlists:
        pid = pl.get("playlistid")
        if pid is None:
            continue
        members, s = stage(f"playlist_members#{pid} {pl.get('name', '')}",
                           lambda pid=pid: d.get_playlist_tracks(pid))
        summaries.append(s)

    queue, s = stage("read_current_queue", d.read_current_queue)
    summaries.append(s)

    slow = sorted((r for r in records if r["kind"] == "read"),
                  key=lambda r: r["sec"], reverse=True)[:20]
    print("\n=== 最慢的 20 次 bulk read ===", flush=True)
    for r in slow:
        print(f"  {r['stage']:28s} {r['sec']:7.3f}s  size={r['size']}  bytes={r.get('bytes')}",
              flush=True)
    total_reads = [r for r in records if r["kind"] == "read"]
    print(f"\n总 bulk read 次数={len(total_reads)}, 总耗时="
          f"{sum(r['sec'] for r in total_reads):.3f}s", flush=True)
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
    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"summaries": summaries, "records": records}, f,
                      ensure_ascii=False, indent=2)
        print("saved:", OUT_JSON, flush=True)
    except Exception as ex:
        print("save fail:", ex, flush=True)
    time.sleep(0.3)
    try:
        v = pde.NJB3Device(timeout=2500)
        v.open()
        st = v.get_play_status()
        print("VERIFY OK state:", st.get("state"), flush=True)
        v.close()
    except Exception as ex:
        print("VERIFY FAIL:", type(ex).__name__, ex, flush=True)
