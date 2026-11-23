"""受控实测：播放中途 play_track 另一首，设备是立刻切歌还是排队？
全程 try/finally：STOP + clear + recover + close + 复核。输出不经管道截断。
"""
import sys, time
import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

d = pde.NJB3Device(timeout=3000)
try:
    d.open()
    print("open OK", flush=True)
    tracks = d.list_tracks(extended=True)
    cand = [t for t in tracks
            if isinstance(t.get("length"), (int, float)) and 20 <= t["length"] <= 90]
    cand.sort(key=lambda t: t["length"])
    a, b = cand[0], cand[1]
    print(f"A id={a['trackid']} {a['length']}s 《{a.get('title','?')}》", flush=True)
    print(f"B id={b['trackid']} {b['length']}s 《{b.get('title','?')}》", flush=True)

    def snap(tag):
        try:
            o = d.observe_playback()
            print(f"[{tag:>8}] t={time.monotonic()-t0:5.1f} state={o['state']} "
                  f"elapsed={o['elapsed']} qpos={o['queue_pos']}", flush=True)
        except Exception as e:
            print(f"[{tag}] snap fail {type(e).__name__} {e}", flush=True)

    d.clear_play_queue()
    t0 = time.monotonic()
    d.play_track(a["trackid"])
    print(f"play A done t={time.monotonic()-t0:.2f}", flush=True)

    # 播 A 到第 6 秒
    for _ in range(6):
        time.sleep(1.0)
        snap("A播放中")
    # 中途切 B
    tb = time.monotonic()
    d.play_track(b["trackid"])
    print(f"play B done 耗时{time.monotonic()-tb:.2f}s t={time.monotonic()-t0:.1f}", flush=True)
    snap("切B即刻")
    # 观察 12 秒，看 elapsed 是归零重来(立刻切)还是继续涨(排队)
    for i in range(12):
        time.sleep(1.0)
        snap(f"切B后{i+1}")
except Exception as ex:
    print("RUN FAIL:", type(ex).__name__, ex, flush=True)
finally:
    for label, fn in (
        ("PLAY_STOP", lambda: d.play_control(pde.NJB3Device.PLAY_STOP)),
        ("clear_play_queue", d.clear_play_queue),
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
        s = v.get_play_status()
        print(f"VERIFY OK state={s.get('state')}", flush=True)
        v.close()
    except Exception as ex:
        print("VERIFY FAIL:", type(ex).__name__, ex, flush=True)
