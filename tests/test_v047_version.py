# -*- coding: utf-8 -*-
"""v0.1.1-261123：验证版本号常量与状态栏显示。"""
import sys, os, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if p not in sys.path:
        sys.path.insert(0, p)

import tkinter as tk
import njb3_studio as studio

ok = bad = 0
def chk(name, cond):
    global ok, bad
    if cond:
        ok += 1; print(f"  [OK] {name}")
    else:
        bad += 1; print(f"  [FAIL] {name}")

print("=== 1. 版本常量 ===")
chk(f"APP_VERSION = 0.1.1-261123", studio.APP_VERSION == "0.1.1-261123")
chk(f"APP_BUILD_TAG = 156300by[AI]", studio.APP_BUILD_TAG == "156300by[AI]")

print("=== 2. 状态栏版本号 Label ===")
root = tk.Tk()
root.geometry("1280x720")
app = studio.StudioApp(root)
root.update(); time.sleep(0.2); root.update()

# 遍历所有 Label，找版本号
ver_text = f"版本：{studio.APP_VERSION} {studio.APP_BUILD_TAG}"
found = []
def walk(w):
    for c in w.winfo_children():
        try:
            if "text" in c.keys() and ver_text in str(c.cget("text")):
                found.append(c)
        except Exception:
            pass
        walk(c)
walk(root)
chk("状态栏含版本号 Label", len(found) >= 1)
if found:
    chk("版本号 Label 可见", found[0].winfo_ismapped() == 1)
    print(f"       文案: {found[0].cget('text')}")

# 默认控制工作台仍生效
chk("默认控制工作台可见", app.dock.panels["other"].outer.winfo_ismapped() == 1)

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(0 if bad == 0 else 1)
