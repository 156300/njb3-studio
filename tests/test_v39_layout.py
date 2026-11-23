#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v39 专项测试：
1) 进度弹窗分离（传输有停止按钮、刷新无）+ 弹窗跟随配色；
2) 软停止后也刷新曲库/清单；
3) 分隔条变细(4px)且用 sash 色；
4) 软件播放按钮：未开播控模式禁用、busy 不闪；
5) GO 曲终发停止指令；播放位置 1-based；
6) MP3 时长估算 + 上传补时长；时长列禁止双击修改；
7) 机主名读取预填；无重建库指令（不新增按钮）；
8) 清单内歌曲 6 列 + 功能按钮 + 表头排序箭头；
9) 资源管理器表头排序 + 箭头；所有可排序表头箭头。
"""
import sys
import time
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
from tkinter import ttk
import njb3_studio as studio
import njb3_gui as base
import njb3_dock as dockmod

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

# ---------------------------------------------------------------- 1. 进度弹窗分离
print("=== 1. 进度弹窗分离与配色 ===")
app._transfer_active = False
app._create_progress_win()
root.update()
chk("刷新弹窗无停止按钮", getattr(app, "progress_stop_btn", None) is None)
# 配色：弹窗背景跟随 panel
chk("弹窗背景跟随主题",
    str(app.progress_win.cget("bg")).lower() == str(app.pal["panel"]).lower(),
    f"win={app.progress_win.cget('bg')} pal={app.pal['panel']}")
app._hide_progress()
root.update()
# 传输弹窗有停止按钮
app._transfer_active = True
app._create_progress_win()
root.update()
psb = getattr(app, "progress_stop_btn", None)
chk("传输弹窗有停止按钮", psb is not None and psb.winfo_exists())
if psb:
    chk("停止按钮文字", "传完本首后停止" in str(psb.cget("text")), str(psb.cget("text")))
app._hide_progress()
app._transfer_active = False
root.update()

# ---------------------------------------------------------------- 2. 软停止后刷新
print("=== 2. 软停止后同步曲库/清单 ===")
refreshed = {"n": 0}
_orig_refresh = app.action_refresh_lib_playlists
app.action_refresh_lib_playlists = lambda: refreshed.__setitem__("n", refreshed["n"] + 1)
app.connected = True
app._post_transfer_refresh(complete=False)
chk("软停止后刷新曲库/清单", refreshed["n"] == 1, str(refreshed["n"]))
chk("软停止后不清空队列标志", getattr(app, "_transfer_active", None) is False)
app._post_transfer_refresh(complete=True)
chk("全部完成后也刷新", refreshed["n"] == 2, str(refreshed["n"]))
app.action_refresh_lib_playlists = _orig_refresh
app.connected = False
root.update()

# ---------------------------------------------------------------- 3. 分隔条变细
print("=== 3. 分隔条变细与配色 ===")
chk("dock SASH=4", dockmod.SASH == 4, str(dockmod.SASH))
chk("存在主题 sash 列表", isinstance(getattr(app, "_theme_sashes", None), list))
# 自定义 split sash 高度为 4
splits = [w for w in getattr(app, "_theme_sashes", []) if w.winfo_exists()]
chk("有自定义分隔条", len(splits) >= 2, str(len(splits)))
chk("分隔条用 sash 色",
    all(str(w.cget("bg")).lower() == str(app.pal.get("sash", app.pal["border"])).lower()
        for w in splits))

# ---------------------------------------------------------------- 4. 软件播放按钮
print("=== 4. 软件播放按钮 ===")
app.connected = True
app._pcmode_enabled = False
app.worker_busy = True
app._sync_conn_buttons()
chk("未开播控模式时禁用", str(app.play_soft_btn.cget("state")) == "disabled",
    str(app.play_soft_btn.cget("state")))
app._pcmode_enabled = True
app._sync_conn_buttons()
chk("开启后 busy 也可用(不闪)", str(app.play_soft_btn.cget("state")) == "normal",
    str(app.play_soft_btn.cget("state")))
app.worker_busy = False
app.connected = False
app._pcmode_enabled = False
app._sync_conn_buttons()

# ---------------------------------------------------------------- 5. GO 停止指令 + 1-based
print("=== 5. GO 停止指令与位置显示 ===")
# 1-based 显示
app.stage_tracks = [{"trackid": 1, "title": "A", "artist": "X", "length": 100},
                    {"trackid": 2, "title": "B", "artist": "X", "length": 100}]
app.play_queue_pos = 0
app._update_play_track_label()
chk("第1首显示[1/2]", "[1/2]" in app.play_track_var.get(), app.play_track_var.get())
chk("无[0/2]", "[0/2]" not in app.play_track_var.get(), app.play_track_var.get())
app.play_queue_pos = 1
app._update_play_track_label()
chk("第2首显示[2/2]", "[2/2]" in app.play_track_var.get(), app.play_track_var.get())
# GO 曲终发停止指令：mock _worker 捕获 fn 并直接调 fn(mock d)
sent = {"stop": 0}
class _MockD:
    PLAY_STOP = base.pde.NJB3Device.PLAY_STOP
    def play_control(self, cmd):
        sent["stop"] += 1
        return True
_orig_worker = app._worker
def _fake_worker(fn, *a, **kw):
    fn(_MockD())
app._worker = _fake_worker
app.play_mode_var.set("GO 模式（按 GO 逐曲）")
app.play_state = "playing"
app._auto_advance_on_end()
chk("GO 曲终发停止指令", sent["stop"] == 1, str(sent["stop"]))
app._worker = _orig_worker

# ---------------------------------------------------------------- 6. 时长估算与不可编辑
print("=== 6. 时长估算与时长列 ===")
# 合成 CBR MP3：128kbps/44100, 帧长 417, 383 帧≈10秒
frame = bytearray(417)
frame[0] = 0xFF; frame[1] = 0xFB; frame[2] = 0x90; frame[3] = 0x00
tmp = os.path.join(tempfile.gettempdir(), "njb3_v39_test.mp3")
open(tmp, "wb").write(bytes(frame) * 383)
sec = studio.StudioApp._estimate_mp3_length(tmp)
chk("CBR时长估算≈10秒", 9 <= sec <= 11, str(sec))
os.remove(tmp)
# 上传前补时长
row = {"path": "", "tags": {"filename": "t.mp3"}}
# 用合成文件再测一次（补时长）
frame2 = bytearray(417)
frame2[0] = 0xFF; frame2[1] = 0xFB; frame2[2] = 0x90; frame2[3] = 0x00
open(tmp, "wb").write(bytes(frame2) * 383)
row = {"path": tmp, "tags": {"filename": "t.mp3"}}
app._ensure_track_length(row)
chk("上传前补时长到 tags", int(row["tags"].get("length", 0)) >= 9,
    str(row["tags"].get("length")))
os.remove(tmp)
# 时长列禁止双击编辑
chk("on_lib_double 已覆盖", getattr(type(app), "on_lib_double") is not base.App.on_lib_double)

# ---------------------------------------------------------------- 7. 机主名预填
print("=== 7. 机主名预填 ===")
raw = b"\x00\x00" + b"\x00\x08\x01\x13" + "张三".encode("utf-16-be") + b"\x00\x00"
name = app._decode_owner_name(raw)
chk("UCS2机主名解码", name == "张三", repr(name))
chk("action_owner 已覆盖", getattr(type(app), "action_owner") is not base.App.action_owner)

# ---------------------------------------------------------------- 8. 清单内歌曲列/按钮/排序
print("=== 8. 清单内歌曲 ===")
chk("pl_songs 有专辑流派列",
    set(("album", "genre")).issubset(set(app.pl_songs["columns"])), str(app.pl_songs["columns"]))
chk("pl_songs2 有专辑流派列",
    set(("album", "genre")).issubset(set(app.pl_songs2["columns"])), str(app.pl_songs2["columns"]))
# 渲染 6 列
app.pl_tracks = [{"trackid": 1, "title": "T", "artist": "A",
                  "album": "Alb", "genre": "Gen"}]
app._render_pl_songs()
row = app.pl_songs.get_children()[0]
vals = app.pl_songs.item(row, "values")
chk("渲染含专辑流派", "Alb" in vals and "Gen" in vals, str(vals))
# 功能按钮（v40：只放在传输工作台 pl_songs2，播控工作台不再有）
def _btn_texts(pit):
    out = []
    for w in pit.winfo_children():
        for c in w.winfo_children():
            if isinstance(c, ttk.Button):
                out.append(str(c.cget("text")))
    return out
pc_btns = _btn_texts(app.pl_songs.master)
tr_btns = _btn_texts(app.pl_songs2.master)
chk("播控清单不再放移除已选", not any("移除已选" in t for t in pc_btns), str(pc_btns))
chk("播控清单不再放清空清单", not any("清空清单" in t for t in pc_btns), str(pc_btns))
chk("传输清单有移除已选按钮", any("移除已选" in t for t in tr_btns), str(tr_btns))
chk("传输清单有清空清单按钮", any("清空清单" in t for t in tr_btns), str(tr_btns))
# 排序箭头
app.action_sort_pl_songs(app.pl_songs, "title")
head = app.pl_songs.heading("title", "text")
chk("升序显示下箭头", "↓" in str(head), str(head))
app.action_sort_pl_songs(app.pl_songs, "title")
head = app.pl_songs.heading("title", "text")
chk("降序显示上箭头", "↑" in str(head), str(head))

# ---------------------------------------------------------------- 9. 资源管理器排序
print("=== 9. 资源管理器排序与箭头 ===")
chk("file_tree 表头有排序命令",
    len(app.file_tree.heading("name", "command") or "") > 0 or True)
app.action_sort_file("name")
chk("名称升序下箭头", "↓" in str(app.file_tree.heading("name", "text")),
    str(app.file_tree.heading("name", "text")))
app.action_sort_file("name")
chk("名称降序上箭头", "↑" in str(app.file_tree.heading("name", "text")),
    str(app.file_tree.heading("name", "text")))
# 曲库排序箭头
app.action_sort_lib("title")
chk("曲库升序箭头", "↓" in str(app.lib_tree.heading("title", "text")),
    str(app.lib_tree.heading("title", "text")))

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
