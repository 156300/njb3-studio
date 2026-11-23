#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v38 专项测试：
1) 传输进度弹窗内“传完本首后停止”按钮位置/状态联动；
2) 折叠侧栏 sash 锁定 14px、箭头无底色、资源管理器折叠后题头清空；
3) 软件播放按钮不随 worker_busy 闪烁；
4) GO 曲终停止、随机播放/循环标记算法、上一曲下一曲基于当前播放曲目。
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

# ---------------------------------------------------------------- 1. 进度弹窗按钮
print("=== 1. 进度弹窗内“传完本首后停止”按钮 ===")
chk("主界面停止按钮已隐藏", not app.stop_btn.winfo_ismapped())
chk("存在 _sync_stop_buttons", callable(getattr(app, "_sync_stop_buttons", None)))
app._sync_stop_buttons("normal")
chk("同步后主按钮 normal", str(app.stop_btn.cget("state")) == "normal")
app._create_progress_win()
root.update()
chk("进度弹窗已创建", app.progress_win is not None)
# v39：非传输任务（刷新曲库等）的弹窗不应有停止按钮。
psb = getattr(app, "progress_stop_btn", None)
chk("刷新弹窗无停止按钮", psb is None, str(psb))
app._hide_progress()
root.update()

# ---------------------------------------------------------------- 2. 折叠侧栏
print("=== 2. 折叠侧栏锁定与题头清空 ===")
# sash 锁定：_pin_folded_sash 已绑定（用绑定回调数量间接验证）
chk("存在 _pin_folded_sash", callable(getattr(app, "_pin_folded_sash", None)))
chk("传输工作台 sash 已绑定锁定",
    len(app.workbench_paned.bind("<B1-Motion>")) > 0)
chk("播控工作台 sash 已绑定锁定", len(app.pc_root.bind("<B1-Motion>")) > 0)

# 资源管理器折叠后题头内容清空
app.action_explorer_fold()
root.update()
ex_title = app.explorer_title
mapped_children = [w for w in ex_title.winfo_children() if w.winfo_ismapped()]
chk("资源管理器折叠后题头内容清空", len(mapped_children) == 0,
    str([str(w.cget('text')) for w in mapped_children if 'text' in w.keys()]))
chk("资源管理器折叠后有竖线箭头", "explorer" in app._wb_handles)
hb = app._wb_handles["explorer"][1]
chk("折叠箭头无底色", str(hb.cget("bg")) == str(app.pal["bg"]).lower() or
    str(hb.cget("bg")).lower() == str(app.pal["bg"]).lower(),
    f"bg={hb.cget('bg')} pal={app.pal['bg']}")
app.action_explorer_fold()
root.update()
# v39：需在 machine 工作台激活状态下验证映射（默认页签可能不是 machine）。
app.action_show_main("machine")
root.update()
chk("资源管理器展开后题头恢复",
    any(w.winfo_ismapped() for w in ex_title.winfo_children()))

# 播控侧栏折叠箭头无底色
app.action_pc_fold_left()
root.update()
pb = app._pc_handles["left"][1]
chk("播控折叠箭头无底色",
    str(pb.cget("bg")).lower() == str(app.pal["bg"]).lower(),
    f"bg={pb.cget('bg')} pal={app.pal['bg']}")
app.action_pc_unfold_left()
root.update()

# ---------------------------------------------------------------- 3. 软件播放不闪烁
print("=== 3. 软件播放按钮不随 busy 闪烁 ===")
app.connected = True
app.worker_busy = True
app._pcmode_enabled = True    # v39：软件播放要求播控模式已开启
app._sync_conn_buttons()
chk("busy 时软件播放仍可用(不闪)", str(app.play_soft_btn.cget("state")) == "normal",
    str(app.play_soft_btn.cget("state")))
app._pcmode_enabled = False
app._sync_conn_buttons()
chk("播控模式未开启时软件播放禁用", str(app.play_soft_btn.cget("state")) == "disabled",
    str(app.play_soft_btn.cget("state")))
app.worker_busy = False
app.connected = False
app._sync_conn_buttons()
chk("未连接时软件播放禁用", str(app.play_soft_btn.cget("state")) == "disabled")

# ---------------------------------------------------------------- 4. 播放模式
print("=== 4. GO 停止 / 随机标记 / 上下曲基准 ===")
# 准备 5 首曲目
app.stage_tracks = [
    {"trackid": 100 + i, "title": f"T{i}", "artist": "A", "length": 100}
    for i in range(5)
]
app._played_marks = set()

# GO：曲终停止，不自动下一首
app.play_mode_var.set("GO 模式（按 GO 逐曲）")
app.action_stage_go(1, manual=True)
chk("GO 逐曲装载", app._auto_loaded is False and app._device_order == [1],
    f"auto={app._auto_loaded} order={app._device_order}")
app._auto_advance_on_end()
chk("GO 曲终停止", app.play_state == "stopped", str(app.play_state))
chk("GO 不自动下一首", app._play_stage_index == 1, str(app._play_stage_index))

# 随机播放：标记未播曲目，全部播完停止
app.play_mode_var.set("随机播放（随机一遍后停）")
app._played_marks = set()
app.action_stage_go(0, manual=True)
chk("随机模式逐曲装载", app._auto_loaded is False and app._device_order == [0],
    f"auto={app._auto_loaded} order={app._device_order}")
# 人为操控清空标记：先加一个标记，再点下一曲应清空
app._played_marks.add(3)
app.action_stage_step(1)
chk("人为操控清空标记", len(app._played_marks) == 0, str(app._played_marks))

# 模拟：连续曲终，收集被播放的位置，验证全部覆盖且不重复（每首至少一遍）
import random as _rnd
_rnd.seed(7)
seen = []
app.worker_busy = False
app._played_marks = set()
app.action_stage_go(2, manual=True)   # 从 index 2 开始
for _ in range(20):
    seen.append(app._play_stage_index)
    if len(app._played_marks) >= 5:
        break
    app.play_state = "playing"        # 屏蔽异步 worker 对状态的干扰
    app._auto_advance_on_end()
chk("随机播放覆盖全部曲目", set(seen) == {0, 1, 2, 3, 4}, str(seen))
# 最后一首播完后应停止
app.play_state = "playing"
app._auto_advance_on_end()
chk("随机播放播完停止", app.play_state == "stopped", str(app.play_state))

# 随机循环：播完后清空标记重来（不停止）
app.play_mode_var.set("随机重复（随机循环）")
app._played_marks = set(range(5))  # 假设已全播完
app._play_stage_index = 4
app.play_state = "playing"
app._auto_advance_on_end()
chk("随机循环播完后清空标记重来",
    len(app._played_marks) <= 1 and app.play_state != "stopped",
    f"marks={app._played_marks} state={app.play_state}")

# 上一曲/下一曲基于当前播放曲目（所有模式）
app.play_mode_var.set("顺序播放（放完停止）")
app.stage_tracks = [
    {"trackid": 200 + i, "title": f"S{i}", "artist": "A", "length": 100}
    for i in range(6)
]
app._play_stage_index = 2   # 当前播放第 3 首
app.stage_index = 5         # 目标/底图选中第 6 首
app.action_stage_step(1)
chk("下一曲基于当前播放曲目", app._play_stage_index == 3,
    f"idx={app._play_stage_index} stage_index={app.stage_index}")
app._play_stage_index = 2
app.stage_index = 5
app.action_stage_step(-1)
chk("上一曲基于当前播放曲目", app._play_stage_index == 1,
    f"idx={app._play_stage_index} stage_index={app.stage_index}")

# 不显式 destroy：本测试会注册播放定时器，销毁时 Tcl 命令重复释放
# 在部分 Windows Python 3.13 环境下会触发崩溃；直接退出由解释器统一清理。
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
