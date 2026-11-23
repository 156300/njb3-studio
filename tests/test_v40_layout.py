#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v40 专项测试：
1) 传输页四按钮/清单改名删除只读副套树；重绘后选中不丢；
2) 移除已选/清空清单只在传输页；
3) 断开/音量/EAX 不随播放同步 busy 闪烁；
4) 机主名写帧按 libnjb 格式、读帧按 strlen 解析；
5) 七种播放模式的逐曲规则；GO 后选定点前进、软件播放不前进；
6) seek/换曲后内部计时归零重建。
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
from tkinter import ttk
import njb3_studio as studio
import njb3_gui as base

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name} {detail}")


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.update()
time.sleep(0.3)
root.update()

# ---------------------------------------------------------------- 1. 选中与按钮归属
print("=== 1. 机器内歌曲选中与按钮绑定 ===")
app.tracks = [{"trackid": 4242, "title": "测试曲", "artist": "A",
               "length": 100, "size": 1024}]
app._render_lib()
app.lib_tree2.selection_set("4242")
seen = {"tree": None}
_orig_del = app.action_delete_tracks
app.action_delete_tracks = lambda: seen.__setitem__("tree", app.lib_tree)
for w in app.lib_action_bar2(app.lib_tree2.master.master).winfo_children():
    if isinstance(w, ttk.Button) and "删除选中" in str(w.cget("text")):
        w.invoke()
app.action_delete_tracks = _orig_del
chk("删除选中读副套曲库树", seen["tree"] is app.lib_tree2, str(seen["tree"]))
app._render_lib()
chk("曲库重绘后保留选中", "4242" in app.lib_tree2.selection(),
    str(app.lib_tree2.selection()))

app.playlists = [{"playlistid": 7, "name": "测试清单"}]
app.pl_members = {}
app._render_playlists()
app.pl_tree2.selection_set("pl::7")
seen["pl"] = None
_orig_ren = app.action_rename_playlist
app.action_rename_playlist = lambda: seen.__setitem__("pl", app.pl_tree)
pbar = app.pl_tree2.master.winfo_children()[-1]
for w in pbar.winfo_children():
    if isinstance(w, ttk.Button) and "改名" in str(w.cget("text")):
        w.invoke()
app.action_rename_playlist = _orig_ren
chk("清单改名读副套清单树", seen["pl"] is app.pl_tree2, str(seen["pl"]))
app._render_playlists()
chk("清单重绘后保留选中", "pl::7" in app.pl_tree2.selection(),
    str(app.pl_tree2.selection()))

app.pl_tracks = [{"trackid": 4242, "title": "测试曲", "artist": "A",
                  "album": "B", "genre": "G"}]
app._render_pl_songs()
app.pl_songs2.selection_set("ps::0")
app._render_pl_songs()
chk("清单内歌曲重绘后保留选中", "ps::0" in app.pl_songs2.selection(),
    str(app.pl_songs2.selection()))

# ---------------------------------------------------------------- 2. 按钮位置
print("=== 2. 移除已选/清空清单归属 ===")


def _btns(pit):
    return [str(c.cget("text")) for w in pit.winfo_children()
            for c in w.winfo_children() if isinstance(c, ttk.Button)]


tr = _btns(app.pl_songs2.master)
pc = _btns(app.pl_songs.master)
chk("传输工作台有移除已选", any("移除已选" in t for t in tr), str(tr))
chk("传输工作台有清空清单", any("清空清单" in t for t in tr), str(tr))
chk("播控工作台无移除已选", not any("移除已选" in t for t in pc), str(pc))
chk("播控工作台无清空清单", not any("清空清单" in t for t in pc), str(pc))

# ---------------------------------------------------------------- 3. 不闪控件
print("=== 3. 断开/音量/EAX 不随 busy 闪烁 ===")
app.connected = True
app.worker_busy = True
app._transfer_active = False
app._sync_conn_buttons()
chk("busy 时断开设备仍可点", str(app.disconnect_btn.cget("state")) == "normal",
    str(app.disconnect_btn.cget("state")))
chk("busy 时音量条仍可点", str(app.vol2_scale.cget("state")) == "normal",
    str(app.vol2_scale.cget("state")))
chk("busy 时 EAX 仍可点", str(app.eax2_chk.cget("state")) == "normal",
    str(app.eax2_chk.cget("state")))
app._handle(("eax_read", None))
chk("EAX 未开放也不闪禁用", str(app.eax2_chk.cget("state")) == "normal",
    str(app.eax2_chk.cget("state")))
app._transfer_active = True
app._sync_conn_buttons()
chk("传歌中断开禁用", str(app.disconnect_btn.cget("state")) == "disabled")
app._transfer_active = False
app.worker_busy = False
app.connected = False
app._sync_conn_buttons()

# ---------------------------------------------------------------- 4. 机主名
print("=== 4. 机主名读写帧 ===")
captured = {}


class _OwnerD:
    def send_only(self, data):
        captured["cmd"] = bytes(data)

    def _status_only(self):
        return 0


studio.StudioApp._write_owner_frame(_OwnerD(), "TESTOWNER")
cmd = captured["cmd"]
u = base.pde.str_to_ucs2("TESTOWNER")
chk("写帧长度=UCS2+尾零(libnjb)", int.from_bytes(cmd[4:6], "big") == len(u) + 2,
    f"{int.from_bytes(cmd[4:6], 'big')} vs {len(u) + 2}")
chk("写帧尾部补 00 00", cmd.endswith(u + b"\x00\x00"), cmd.hex())
raw = b"\x00\x00" + (len(u) + 2).to_bytes(2, "big") + b"\x01\x13" + u + b"\x00\x00"
chk("读帧按 strlen 解析", app._decode_owner_name(raw) == "TESTOWNER",
    repr(app._decode_owner_name(raw)))

# ---------------------------------------------------------------- 5. 播放模式
print("=== 5. 七种播放模式 ===")
MODES = {label: key for label, key in app.PLAY_MODES}
app.stage_tracks = [
    {"trackid": 100 + i, "title": f"T{i}", "artist": "A", "length": 100}
    for i in range(4)
]
stops = []


class _PlayD:
    PLAY_STOP = base.pde.NJB3Device.PLAY_STOP

    def play_track(self, tid):
        return True

    def play_control(self, cmd):
        stops.append(cmd)
        return True

    def seek(self, ms):
        return True


app._worker = lambda fn, *a, **kw: fn(_PlayD())
app._played_marks = set()

# GO：任意模式点 GO 播放当前选定，选定跳下一首
for label in ("GO 模式（按 GO 逐曲）", "单曲（放完停止）", "顺序播放（放完停止）",
              "随机播放（随机一遍后停）", "随机重复（随机循环）"):
    app.play_mode_var.set(label)
    app.stage_index = 1
    app.action_stage_go()
    chk(f"GO({MODES[label]})播放当前选定并跳下一",
        app._play_stage_index == 1 and app.stage_index == 2,
        f"play={app._play_stage_index} sel={app.stage_index}")
# 软件播放：显式 index，不跳选定点
app.play_mode_var.set(MODES and "GO 模式（按 GO 逐曲）")
_orig_sel = app._selected_track_anywhere
app._selected_track_anywhere = lambda: (app.stage_tracks[3], "stage")
app.stage_index = 1
app.action_play_selected()
chk("软件播放当前选定不跳下一", app.stage_index == 3 and app._play_stage_index == 3,
    f"sel={app.stage_index} play={app._play_stage_index}")
app._selected_track_anywhere = _orig_sel

# 单曲：曲终不发停止指令
app.play_mode_var.set("单曲（放完停止）")
stops.clear()
app._play_stage_index = 2
app._boundary_handled = False
app.play_state = "playing"
app._auto_advance_on_end()
chk("单曲曲终不发停止指令", not stops and app.play_state == "stopped",
    f"stops={stops} state={app.play_state}")

# 单曲重复：重播当前曲目
app.play_mode_var.set("单曲重复（反复当前）")
app._play_stage_index = 2
app.stage_index = 2
app._boundary_handled = False
app.play_state = "playing"
app._auto_advance_on_end()
chk("单曲重复重播当前曲", app._play_stage_index == 2 and not stops,
    f"idx={app._play_stage_index} stops={stops}")

# 顺序：播放下一首，播完最后一条停止
app.play_mode_var.set("顺序播放（放完停止）")
app._play_stage_index = 0
app.stage_index = 1
app._boundary_handled = False
app.play_state = "playing"
app._auto_advance_on_end()
chk("顺序曲终播放选定并跳下一", app._play_stage_index == 1 and app.stage_index == 2,
    f"play={app._play_stage_index} sel={app.stage_index}")
app._play_stage_index = 3
app.stage_index = 0
app._boundary_handled = False
app.play_state = "playing"
stops.clear()
app._auto_advance_on_end()
chk("顺序最后一条发停止", len(stops) == 1 and app.play_state == "stopped",
    f"stops={stops}")

# 循环：末条后回到队首，往复不停
app.play_mode_var.set("循环播放（队列循环）")
app._play_stage_index = 3
app.stage_index = 0
app._boundary_handled = False
app.play_state = "playing"
stops.clear()
app._auto_advance_on_end()
chk("循环末条后回队首", app._play_stage_index == 0 and app.stage_index == 1 and not stops,
    f"play={app._play_stage_index} sel={app.stage_index}")

# 随机：逐曲打标，全部播完停止
import random as _rnd
_rnd.seed(11)
app.play_mode_var.set("随机播放（随机一遍后停）")
app._played_marks = set()
app._play_stage_index = 0
app.stage_index = 1
app._boundary_handled = False
app.play_state = "playing"
stops.clear()
seen = []
for _ in range(12):
    app.play_state = "playing"
    app._boundary_handled = False
    app._auto_advance_on_end()
    seen.append(app._play_stage_index)
    if stops:
        break
chk("随机覆盖全部曲目", set(seen) | {0} == {0, 1, 2, 3}, str(seen))
chk("随机全播完发停止", len(stops) == 1 and len(app._played_marks) == 4,
    f"stops={stops} marks={app._played_marks}")

# 随机重复：全标后清标，随机跳非当前
app.play_mode_var.set("随机重复（随机循环）")
app._played_marks = set(range(4))
app._play_stage_index = 3
app.stage_index = 0
app._boundary_handled = False
app.play_state = "playing"
stops.clear()
app._auto_advance_on_end()
chk("随机重复清标重来", not stops and len(app._played_marks) <= 1
    and app._play_stage_index != 3,
    f"marks={app._played_marks} idx={app._play_stage_index}")

# ---------------------------------------------------------------- 6. 计时
print("=== 6. 定位/换曲后计时基准 ===")
app.play_duration_sec = 300
app.play_duration_var.set("05:00")
app.play_pos_var.set(42.0)
app.play_elapsed_sec = 250.0
app.action_seek()
chk("seek 后内部秒数=目标", int(app.play_elapsed_sec) == 42, str(app.play_elapsed_sec))
app.play_mode_var.set("GO 模式（按 GO 逐曲）")
app.play_elapsed_sec = 180.0
app.play_pos_var.set(180.0)
app.action_stage_go(0)
chk("换曲后计时归零", app.play_elapsed_sec == 0.0 and float(app.play_pos_var.get()) == 0.0,
    f"{app.play_elapsed_sec} / {app.play_pos_var.get()}")

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
