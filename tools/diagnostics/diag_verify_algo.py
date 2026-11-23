"""真机验证 v44 算法：本地计时到 total 才切下一首，单一触发。
模拟 GUI 的调度（不启动 GUI），连播 3 首短曲，记录每次曲终时刻与设备实际 elapsed。
全程 try/finally：STOP + clear + recover + close + 复核。不用管道截断输出。
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
            if isinstance(t.get("length"), (int, float)) and 4 <= t["length"] <= 60]
    cand.sort(key=lambda t: t["length"])
    picks = cand[:3]
    for i, t in enumerate(picks):
        print(f"曲目{i}: id={t['trackid']} {t['length']}s 《{t.get('title','?')}》", flush=True)

    d.clear_play_queue()
    total = int(picks[0]["length"])
    t_start = time.monotonic()
    d.play_track(picks[0]["trackid"])
    print(f"play 曲目0 耗时 {time.monotonic()-t_start:.2f}s", flush=True)

    # 模拟 v44：每秒 tick，本地计时到 total 才切；每首都记录真实 elapsed
    cur = 0
    local_elapsed = 0.0
    last_dev_elapsed = -1
    tick_t0 = time.monotonic()
    switched = []
    MAX = sum(int(t["length"]) for t in picks) + 15
    while time.monotonic() - tick_t0 < MAX and cur < len(picks):
        time.sleep(1.0)
        local_elapsed += 1.0
        try:
            o = d.observe_playback()
            de = o["elapsed"]
            print(f"[曲目{cur}] local={local_elapsed:5.1f} dev_elapsed={de} "
                  f"state={o['state']}", flush=True)
            # 检测设备是否已自然归零（说明它先于软件到头，会循环重播）
            if isinstance(de, int) and last_dev_elapsed >= total - 4 and de < 5:
                print(f"  >>> 设备先归零（软件仍在 {local_elapsed:.0f}s）", flush=True)
            last_dev_elapsed = de if isinstance(de, int) else last_dev_elapsed
        except Exception as e:
            print(f"  observe err {type(e).__name__}", flush=True)
        # v44 单一触发：本地到 total 才切下一首
        if local_elapsed >= total:
            nxt = cur + 1
            if nxt >= len(picks):
                print(f"  >>> 曲目{cur} 播完，全列表结束", flush=True)
                break
            tn = time.monotonic()
            d.play_track(picks[nxt]["trackid"])
            print(f"  >>> 切换 曲目{nxt}（本地到头 {local_elapsed:.0f}s，"
                  f"play_track 耗时 {time.monotonic()-tn:.2f}s）", flush=True)
            switched.append((cur, nxt, local_elapsed))
            cur = nxt
            local_elapsed = 0.0
            last_dev_elapsed = -1
            total = int(picks[nxt]["length"])
    print(f"切换记录: {switched}", flush=True)
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
