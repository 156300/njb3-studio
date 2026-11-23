#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""njb3_studio 专项测试：UI 构建 / 停靠面板 / 页签 shim / 主题 / 布局树 / 对比度。
不连接真机，纯界面与逻辑验证。"""
import sys
import os
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk
import njb3_theme as theme
import njb3_dock as dockmod
import njb3_studio as studio

ok = 0
bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name} {detail}")


print("=== 1. 主题系统 ===")
chk("4 套预设 + 跟随系统", len(theme.theme_names()) == 5, str(theme.theme_names()))
aud = theme.audit()
worst = min(min(v.values()) for v in aud.values())
chk("全部对比度 >= 4.5 (WCAG AA)", worst >= 4.5, f"最低 {worst}")
chk("focus_tags 三态齐全", set(theme.focus_tags(theme.THEMES[theme.DEFAULT_THEME])) == {"hot", "cold", "warm"})

print("=== 2. 启动新 UI（无设备）===")
root = tk.Tk()
root.geometry("1360x860")
app = studio.StudioApp(root)
root.update()

chk("StudioApp 构建成功", app is not None)
chk("3 个整页工作台", sorted(app.dock.panels) == ["machine", "other", "playctrl"],
    str(list(app.dock.panels)))
chk("6 个控制模块", sorted(app.control_dock.panels) ==
    ["appearance", "devstat", "driver", "log", "playback", "refresh"],
    str(list(app.control_dock.panels)))
for pid, p in app.dock.panels.items():
    chk(f"整页 {pid} 已挂载", p.outer.winfo_exists() == 1 and p.header is None)
for pid, p in app.control_dock.panels.items():
    chk(f"模块 {pid} 已挂载", p.outer.winfo_exists() == 1 and p.header is not None)

# 关键业务控件属性（证明复用原构建方法）
for attr in ("tree", "lib_tree", "lib_tree2", "pl_tree", "pl_tree2", "pl_songs",
             "pl_songs2", "stage_tree", "folder_tree", "file_tree", "log",
             "status_var", "info_vars", "vol_scale", "eax_chk", "connect_btn", "drv_btn2"):
    chk(f"业务控件属性 {attr}", hasattr(app, attr), "")

chk("nb shim 可用", hasattr(app.nb, "select"))
app.nb.select(app.tab_other)
root.update()
chk("nb.select(other) 不报错", True)

print("=== 3. 布局树操作 ===")
d = app.dock
# 记录初始面板数（树中）
def collect_ids(node, acc):
    if node.get("type") == "tabs":
        acc.extend(node.get("panels", []))
    for c in node.get("children", []):
        collect_ids(c, acc)
    return acc

before = collect_ids(d.tree, [])
chk("初始树覆盖 3 个整页", set(before) == {"machine", "playctrl", "other"}, str(set(before)))

cd = app.control_dock
# 合并：把 refresh 模块合并到 devstat 中心
cd.dock_to("refresh", "devstat", "center")
root.update()
tabs = cd._find_tabs_of("devstat")
chk("模块合并成功", tabs is not None and "refresh" in tabs["panels"],
    str(tabs["panels"]) if tabs else "None")

# 拆出为浮动
cd.to_float("refresh")
root.update()
chk("模块拆出浮动成功", "refresh" in cd.floats)

# 拖到边缘贴合
cd.dock_to("refresh", "devstat", "right")
root.update()
chk("模块边缘贴合成功", "refresh" not in cd.floats
    and cd._find_tabs_of("refresh") is not None)

print("=== 4. 主题切换 ===")
for name in ("深空石墨", "午夜蓝", "云白", "暖米", theme.FOLLOW_SYSTEM):
    app.apply_studio_theme(name, save=False)
    root.update()
    chk(f"切换主题 {name}", app.theme_name == name)

app.apply_studio_theme("深空石墨", save=False)
root.update()

# 清理测试产生的配置，避免污染交付默认
for f in (studio.THEME_CFG, studio.DOCK_CFG, studio.CONTROL_DOCK_CFG):
    try:
        if f.exists():
            f.unlink()
    except Exception:
        pass

root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
