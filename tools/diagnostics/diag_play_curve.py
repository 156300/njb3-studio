"""受控播放时序实测（安全版）。
全程 try/finally：退出前 STOP + recover + close + 复核，绝不靠管道截断输出。
观测：clear_play_queue + play_track 后，state/elapsed/queue_pos 随时间如何变化，
尤其是曲目播完那一刻设备到底怎么表现（这是 v43 乱跳的判定依据）。
"""
import sys, time
import os as _os
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src", "scripts")))
import njb3_pde as pde

d = pde.NJB3Device(timeout=3000)
try:
    d.open()
    print("open OK", flush=True)

    t0 = time.monotonic()
    tracks = d.list_tracks(extended=True)
    print(f"曲库 {len(tracks)} 首，读库耗时 {time.monotonic()-t0:.1f}s", flush=True)
    cand = [t for t in tracks
            if isinstance(t.get("length"), (int, float)) and 5 <= t["length"] <= 90]
    cand.sort(key=lambda t: t["length"])
    if not cand:
        print("没有合适的短曲目", flush=True)
        raise SystemExit
    t = cand[0]
    tid, length = t["trackid"], int(t["length"])
    print(f"选曲 id={tid} 时长={length}s 《{t.get('title','?')}》", flush=True)

    # 先清空队列再播单曲，观察设备播完后的行为
    t0 = time.monotonic()
    try:
        d.clear_play_queue()
        print(f"clear_play_queue 耗时 {time.monotonic()-t0:.3f}s", flush=True)
    except Exception as e:
        print(f"clear_play_queue 失败 {type(e).__name__} {e}", flush=True)

    t0 = time.monotonic()
    ok = d.play_track(tid)
    print(f"play_track 耗时 {time.monotonic()-t0:.3f}s ok={ok}", flush=True)

    start = time.monotonic()
    zero_seen = False
    last_elapsed = None
    fast_phase = False
    samples = 0
    # 采样直到播完后再看 12 秒
    while time.monotonic() - start < length + 20:
        el = time.monotonic() - start
        try:
            obs = d.observe_playback()
        except Exception as e:
            print(f"[{el:6.1f}] observe 失败 {type(e).__name__} {e}", flush=True)
            break
        e = obs.get("elapsed")
        st = obs.get("state")
        q = obs.get("queue_pos")
        print(f"[{el:6.1f}] state={st} elapsed={e} qpos={q}", flush=True)
        samples += 1
        # 进入曲末高频采样
        if isinstance(e, (int, float)) and e >= length - 6:
            fast_phase = True
        # 检测 elapsed 归零（上一采样还是大值，这回接近 0）
        if (last_elapsed is not None and isinstance(last_elapsed, (int, float))
                and last_elapsed >= length - 8 and isinstance(e, (int, float)) and e < 5):
            zero_seen = True
            print(f">>> 曲终归零发生在 t={el:.1f}s（设备标称 {length}s）", flush=True)
        last_elapsed = e
        if zero_seen and el > (length + 12):
            break
        time.sleep(0.3 if fast_phase else 1.0)
    print(f"采样结束，共 {samples} 次，zero_seen={zero_seen}", flush=True)
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
        e = v.get_elapsed_time()
        print(f"VERIFY OK state={s.get('state')} elapsed={e}", flush=True)
        v.close()
    except Exception as ex:
        print("VERIFY FAIL:", type(ex).__name__, ex, flush=True)
