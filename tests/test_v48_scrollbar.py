# -*- coding: utf-8 -*-
"""v0.1.1-261123：验证播控工作台「清单内的歌曲」垂直滚动条可见且不挤成 0 宽。"""
import sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if p not in sys.path:
        sys.path.insert(0, p)

import tkinter as tk
from tkinter import ttk
import njb3_studio as studio

ok = bad = 0
def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name} {detail}")

print("=== 1. 版本常量 ===")
chk("APP_VERSION = 0.1.1-261123", studio.APP_VERSION == "0.1.1-261123",
    str(studio.APP_VERSION))

print("=== 2. 播控页滚动条 ===")
root = tk.Tk()
root.geometry("1360x860")
app = studio.StudioApp(root)
app.nb.select(app.tab_playctrl)
root.update(); time.sleep(0.2); root.update()

tree = getattr(app, "pl_songs", None)
chk("pl_songs 存在", tree is not None)
if tree is not None:
    pit = tree.master
    sbs = [w for w in pit.winfo_children()
           if isinstance(w, ttk.Scrollbar) and str(w.cget("orient")) == "vertical"]
    chk("存在垂直滚动条", len(sbs) == 1, f"找到 {len(sbs)} 个")
    if sbs:
        sb = sbs[0]
        chk("滚动条已映射", sb.winfo_ismapped() == 1,
            f"mapped={sb.winfo_ismapped()}")
        chk("滚动条宽度 >= 10", sb.winfo_width() >= 10,
            f"width={sb.winfo_width()}")
        chk("滚动条高度 >= 100", sb.winfo_height() >= 100,
            f"height={sb.winfo_height()}")
        cmd = str(tree.cget("yscrollcommand"))
        chk("yscrollcommand 已绑定", "set" in cmd, cmd)
        try:
            packed = bool(tree.pack_info())
        except Exception:
            packed = False
        chk("树改用 grid（不再 pack）", not packed)
        # 拖动 sash 改变栏宽后仍然可见
        sash = getattr(app, "pc_left_split", None)
        if sash is not None and hasattr(sash, "_split_apply"):
            sash._split_state["ratio"] = 0.6
            sash._split_apply()
            root.update()
            chk("调整分隔条后仍映射", sb.winfo_ismapped() == 1,
                f"width={sb.winfo_width()}")

print("=== 3. 传输工作台滚动条（v0.1.1 修订） ===")
app.nb.select(app.tab_machine)
root.update(); time.sleep(0.2); root.update()

tree2 = getattr(app, "pl_songs2", None)
chk("pl_songs2 存在", tree2 is not None)
if tree2 is not None:
    pit2 = tree2.master
    sbs2 = [w for w in pit2.winfo_children()
            if isinstance(w, ttk.Scrollbar) and str(w.cget("orient")) == "vertical"]
    chk("传输页存在垂直滚动条", len(sbs2) == 1, f"找到 {len(sbs2)} 个")
    if sbs2:
        sb2 = sbs2[0]
        chk("传输页滚动条已映射", sb2.winfo_ismapped() == 1,
            f"mapped={sb2.winfo_ismapped()}")
        chk("传输页滚动条宽度 >= 10", sb2.winfo_width() >= 10,
            f"width={sb2.winfo_width()}")
        chk("传输页滚动条高度 >= 100", sb2.winfo_height() >= 100,
            f"height={sb2.winfo_height()}")
        cmd2 = str(tree2.cget("yscrollcommand"))
        chk("传输页 yscrollcommand 已绑定", "set" in cmd2, cmd2)
        try:
            packed2 = bool(tree2.pack_info())
        except Exception:
            packed2 = False
        chk("传输页树改用 grid（不再 pack）", not packed2)
        bars = [w for w in pit2.winfo_children()
                if isinstance(w, ttk.Frame)
                and getattr(w, "_pl_song_btnbar", False)]
        chk("移除已选/清空清单按钮条仍在", len(bars) == 1
            and bars[0].winfo_ismapped() == 1, f"找到 {len(bars)} 个")
        sash2 = getattr(app, "transfer_playlist_split", None)
        if sash2 is not None and hasattr(sash2, "_split_apply"):
            sash2._split_state["ratio"] = 0.6
            sash2._split_apply()
            root.update()
            chk("传输页调整分隔条后仍映射", sb2.winfo_ismapped() == 1,
                f"width={sb2.winfo_width()}")

# 默认控制工作台仍生效
chk("默认控制工作台仍在", "other" in app.dock.panels)
root.destroy()
print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(0 if bad == 0 else 1)
