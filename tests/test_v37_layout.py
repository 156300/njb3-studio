#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v37 专项测试：控制页分布/最小尺寸、折叠箭头靠右、传输前停止播放、
清单合并写入、停止按钮改名、音量条与软件播放按钮状态。"""
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


def panels_of(node):
    out = []
    if not isinstance(node, dict):
        return out
    if node.get("type") == "tabs":
        return list(node.get("panels", []))
    for c in node.get("children", []):
        out += panels_of(c)
    return out


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.update()
time.sleep(0.3)
root.update()

print("=== 1. 控制工作台默认分布与最小尺寸 ===")
tree = studio.control_default_tree(list(studio.CONTROL_MODULE_IDS))
# 顶层横向：左列 + 日志在右
chk("控制页顶层为左右分布", tree.get("orient") == "horizontal", str(tree.get("orient")))
left_node = tree["children"][0]
chk("日志在右列", panels_of(tree["children"][1]) == ["log"],
    str(panels_of(tree["children"][1])))
# 左列自上而下包含 devstat/playback，再是 refresh/driver/appearance
all_left = panels_of(left_node)
chk("左列含全部五个模块",
    set(all_left) == {"devstat", "playback", "refresh", "driver", "appearance"},
    str(all_left))
# 实际 dock 树的 min_px 生效
cd = app.control_dock
def find_min(node, pid):
    if not isinstance(node, dict):
        return None
    if node.get("type") == "tabs" and pid in node.get("panels", []):
        return node.get("_dummy")
    for c in node.get("children", []):
        r = find_min(c, pid)
        if r is not None:
            return r
    return None

def collect_paned_mins(node, acc):
    if not isinstance(node, dict) or node.get("type") != "paned":
        return
    acc.append((node.get("orient"), list(node.get("min_px") or []),
                [panels_of(c) for c in node.get("children", [])]))
    for c in node.get("children", []):
        collect_paned_mins(c, acc)

acc = []
collect_paned_mins(cd.tree, acc)
# 只看垂直分割节点中 refresh/driver/appearance 各自对应的 min_px（高度）
big_mins = []
for orient, mins, child_panels in acc:
    if orient != "vertical" or not mins:
        continue
    for idx, ps in enumerate(child_panels):
        if idx < len(mins) and set(ps) & {"refresh", "driver", "appearance"}:
            big_mins.append(mins[idx])
chk("三个易挤框体有足够最小高度",
    len(big_mins) >= 3 and all(m >= 100 for m in big_mins), str(big_mins))

print("=== 2. 折叠箭头标题行撑满靠右 ===")
# 资源管理器标题行
ex_title = getattr(app, "explorer_title", None)
ex_frame = getattr(app, "explorer_frame", None)
chk("资源管理器标题行存在", ex_title is not None)
if ex_title and ex_frame:
    bw = ex_title.winfo_width()
    fw = ex_frame.winfo_width()
    chk("资源管理器标题行撑满框宽", bw >= fw - 24, f"title={bw} frame={fw}")
    btn = app.explorer_fold_btn
    chk("资源管理器折叠箭头在标题行最右",
        btn.winfo_x() + btn.winfo_width() >= bw - 16,
        f"btn_x={btn.winfo_x()} w={btn.winfo_width()} title_w={bw}")
# 播控播放清单标题行
pc_hdr = getattr(app, "pc_left_fold_btn", None)
if pc_hdr:
    hw = pc_hdr.master.winfo_width()
    chk("播放清单折叠箭头靠右",
        pc_hdr.winfo_x() + pc_hdr.winfo_width() >= hw - 16,
        f"btn_x={pc_hdr.winfo_x()} w={pc_hdr.winfo_width()} hdr_w={hw}")

print("=== 3. 停止按钮改名与传输前停止播放 ===")
chk("停止按钮改名为传完本首后停止",
    "传完本首后停止" in str(app.stop_btn.cget("text")),
    str(app.stop_btn.cget("text")))
chk("停止按钮默认禁用", str(app.stop_btn.cget("state")) == "disabled")

# 模拟传输，捕获 fn 与设备调用
captured = []
real_worker = app._worker
app._worker = lambda fn, *a, **kw: captured.append(fn)
app.queue_rows = [
    {"path": "a.mp3", "tags": {"title": "A", "filename": "a.mp3", "size": 100},
     "status": base.STATUS_WAIT, "progress": 0, "target_pl_id": 7,
     "target_pl_name": "清单X"},
]
app.tracks = []  # 无同名文件
app.action_start_transfer()
chk("传输开始时启用停止按钮", str(app.stop_btn.cget("state")) == "normal")
fn = captured[-1]

play_calls = []
add_calls = []
class Dev:
    def play_control(self, cmd):
        play_calls.append(cmd)
        return True
    def upload_track(self, path, tags, progress=None):
        return 501
    def get_playlist_tracks(self, plid):
        return [10, 20, 30]          # 清单原有 3 首
    def add_tracks_to_playlist(self, plid, ids):
        add_calls.append((plid, list(ids)))
        return 9
    def list_playlists(self):
        return [{"playlistid": 9, "name": "清单X"}]

evs = []
app.q.put = lambda item: evs.append(item)
fn(Dev())
chk("传输前先发停止播放指令", play_calls and play_calls[0] == base.pde.NJB3Device.PLAY_STOP,
    str(play_calls))
chk("清单写入含原有+新曲", add_calls and add_calls[0][1] == [10, 20, 30, 501],
    str(add_calls))
chk("本地清单一首不漏（全量替换）",
    any(e[0] == "pl_members_replace" and e[3] == [10, 20, 30, 501] for e in evs),
    str([e for e in evs if e[0] == "pl_members_replace"]))
app.q.put = lambda item: None
app._worker = real_worker

print("=== 4. 音量条与软件播放按钮 ===")
chk("播控音量条加长一倍", app.vol2_scale.cget("length") == 128,
    str(app.vol2_scale.cget("length")))
chk("软件播放按钮存在", hasattr(app, "play_soft_btn"))
# 未连接时应禁用
app.connected = False
app.worker_busy = False
app._sync_conn_buttons()
chk("未连接时软件播放禁用", str(app.play_soft_btn.cget("state")) == "disabled",
    str(app.play_soft_btn.cget("state")))
app.connected = True
app._pcmode_enabled = True   # v39：还需播控模式已开启
app._sync_conn_buttons()
chk("已连接且开启播控时软件播放可用", str(app.play_soft_btn.cget("state")) == "normal",
    str(app.play_soft_btn.cget("state")))

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
