#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""停靠交互测试（v32）：模拟控制工作台模块标题栏拖动，验证贴边、合并、
浮动、尺寸调整在真实事件链下工作。三个整页工作台本身不参与停靠。"""
import os, sys, time, json
from pathlib import Path
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
for p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if p not in sys.path:
        sys.path.insert(0, p)

import tkinter as tk
import njb3_studio as studio

ok = bad = 0


def chk(n, c, d=""):
    global ok, bad
    if c:
        ok += 1
        print(f"  [OK] {n}")
    else:
        bad += 1
        print(f"  [FAIL] {n} {d}")


root = tk.Tk()
root.geometry("1500x900")
app = studio.StudioApp(root)
root.lift()
root.update()
time.sleep(0.2)
root.update()
d = app.control_dock
app.action_show_main("other")
root.update()
time.sleep(0.2)
root.update()

print("=== 模拟模块标题栏拖拽事件链 ===")
scx0 = d.stage.winfo_rootx()
scy0 = d.stage.winfo_rooty()
p = d.panels["driver"]
sx, sy, sw, sh = p.rect
gx = scx0 + sx + sw / 2
gy = scy0 + sy + 12
p._press(type("E", (), {"x_root": gx, "y_root": gy})())
p._motion(type("E", (), {"x_root": gx + 20, "y_root": gy + 10})())
t = d.panels["devstat"]
tx = scx0 + t.rect[0] + t.rect[2] / 2
ty = scy0 + t.rect[1] + t.rect[3] / 2
p._motion(type("E", (), {"x_root": tx, "y_root": ty})())
d.update_drag(p, tx, ty)
p._release(type("E", (), {"x_root": tx, "y_root": ty})())
root.update()
tabs = d._find_tabs_of("devstat")
chk("拖到中心→模块合并", tabs and "driver" in tabs["panels"],
    str(tabs["panels"]) if tabs else "None")

vis = d.panels["driver"]
tx2 = scx0 + vis.rect[0] + vis.rect[2] * 0.9
ty2 = scy0 + vis.rect[1] + vis.rect[3] / 2
hit_pid, hit_zone = d._hit(int(tx2), int(ty2), "playback")
chk("右边缘命中判定", hit_pid == "driver" and hit_zone == "right",
    f"{hit_pid}/{hit_zone}")
d.dock_to("driver", "devstat", "right")
root.update()
chk("拖到边缘→拆出并贴合",
    d._find_tabs_of("driver") is not None and "driver" not in d.floats)

chk("空白命中=None", d._hit(scx0 - 50, scy0 - 50, "driver") == (None, None),
    str(d._hit(scx0 - 50, scy0 - 50, "driver")))
d.to_float("refresh")
root.update()
chk("→浮动", "refresh" in d.floats)

d.save()
chk("布局已写入 JSON", Path(d.config_path).exists())
data = json.load(open(d.config_path, encoding="utf-8"))
chk("布局 JSON 含 tree/floats", "tree" in data and "floats" in data)

for f in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if f.exists():
            f.unlink()
    except Exception:
        pass
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
