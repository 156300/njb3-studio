#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""njb3_layout_editor.py — NJB3 主程序的可视化界面布局编辑器。

可调：资源管理器模式、自动刷新/进度框/搜索框开关、三处分栏比例、
页签顺序、曲库列顺序/显示/宽度、传输列顺序/显示/宽度。
保存后写入 gui_layout.json，重启 njb3_gui.exe 生效。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import njb3_layout as layout_cfg


class LayoutEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("NJB3 界面布局编辑器")
        root.geometry("1060x720")
        root.minsize(900, 620)
        self.path = layout_cfg.config_path()
        try:
            self.data = layout_cfg.load_layout(str(self.path))
        except Exception:
            self.data = layout_cfg.default_layout()

        self.mode = tk.StringVar(value=self.data.get("explorer_mode", "native"))
        self.auto_refresh = tk.BooleanVar(value=self.data.get("auto_refresh_on_connect", True))
        self.show_progress = tk.BooleanVar(value=self.data.get("show_progress_dialog", True))
        self.show_search = tk.BooleanVar(value=self.data.get("show_lib_search", True))
        self.sash_vars = {k: tk.DoubleVar(value=v)
                          for k, v in self.data.get("sash", {}).items()}

        self._build_ui()
        self._load_into_widgets()

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        root = self.root
        top = ttk.Frame(root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="NJB3 界面布局编辑器",
                  font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        ttk.Label(top, text="改完点【保存布局】，然后重启主程序",
                  foreground="#a50").pack(side="left", padx=14)

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(4, 6))
        self.body = body

        left = ttk.Frame(body)
        body.add(left, weight=3)
        right = ttk.Frame(body)
        body.add(right, weight=4)

        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)

        self._build_basic_tab(nb)
        self._build_tab_order_tab(nb)
        self.lib_view = self._build_column_tab(nb, "曲库列", "lib_columns")
        self.queue_view = self._build_column_tab(nb, "传输列", "queue_columns")

        pv = ttk.LabelFrame(right, text=" 布局预览（示意图，按当前设置实时更新） ", padding=(8, 6))
        pv.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(pv, bg="#F7F7F7", highlightthickness=1,
                                highlightbackground="#BBB")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

        note = ttk.Label(right, text=(
            "说明：\n"
            "· 曲库固定序号列 # 不能隐藏，排序时它始终代表机器内部顺序。\n"
            "· 原生资源管理器 = Windows 自己的 Explorer 窗口（左侧树 + 右侧详细文件）。\n"
            "· 这里只改布局，不改播放器数据；保存后请重启 njb3_gui.exe。"),
            justify="left", foreground="#555")
        note.pack(fill="x", pady=(8, 0))

        bar = ttk.Frame(root, padding=(8, 0, 8, 8))
        bar.pack(fill="x")
        self.path_var = tk.StringVar(value=str(self.path))
        ttk.Label(bar, textvariable=self.path_var, foreground="#666").pack(
            side="left", fill="x", expand=True)
        ttk.Button(bar, text="打开布局…", command=self.open_layout).pack(side="right", padx=3)
        ttk.Button(bar, text="恢复默认", command=self.reset_default).pack(side="right", padx=3)
        ttk.Button(bar, text="保存布局", command=self.save_layout).pack(side="right", padx=3)

    def _build_basic_tab(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="基本设置")

        ttk.Label(f, text="资源管理器模式：", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        ttk.Radiobutton(f, text="原生 Windows 资源管理器（推荐，左侧树 + 右侧详细文件栏）",
                        variable=self.mode, value="native",
                        command=self.redraw).pack(anchor="w", pady=2)
        ttk.Radiobutton(f, text="内置简化目录树（兼容回退用）",
                        variable=self.mode, value="legacy",
                        command=self.redraw).pack(anchor="w", pady=2)

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(f, text="连接后自动刷新曲库和播放列表",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        ttk.Checkbutton(f, text="启用（连接成功后自动全量读取）",
                        variable=self.auto_refresh, command=self.redraw).pack(anchor="w", pady=2)
        ttk.Checkbutton(f, text="长通讯任务弹进度框，完成后自动关闭",
                        variable=self.show_progress, command=self.redraw).pack(anchor="w", pady=2)
        ttk.Checkbutton(f, text="曲库搜索栏常驻显示",
                        variable=self.show_search, command=self.redraw).pack(anchor="w", pady=2)

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(f, text="分栏比例", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        specs = (("main_explorer", "左侧资源管理器宽度"),
                 ("machine_lib", "机器歌曲页：曲库区宽度"),
                 ("other_log", "其他功能页：功能区高度"))
        for key, label in specs:
            row = ttk.Frame(f); row.pack(fill="x", pady=5)
            ttk.Label(row, text=label, width=28).pack(side="left")
            sc = ttk.Scale(row, from_=0.15, to=0.85, orient="horizontal",
                           variable=self.sash_vars[key], command=lambda _v: self.redraw())
            sc.pack(side="left", fill="x", expand=True)
            ttk.Label(row, textvariable=self.sash_vars[key], width=6).pack(side="left")

    def _build_tab_order_tab(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="页签顺序")
        ttk.Label(f, text="右侧三个页签的排列顺序：").pack(anchor="w")
        self.tab_list = tk.Listbox(f, height=6, activestyle="none")
        self.tab_list.pack(fill="both", expand=True, pady=6)
        bar = ttk.Frame(f); bar.pack(fill="x")
        ttk.Button(bar, text="↑ 上移", command=lambda: self._move_list(self.tab_list, -1)).pack(side="left", padx=3)
        ttk.Button(bar, text="↓ 下移", command=lambda: self._move_list(self.tab_list, 1)).pack(side="left", padx=3)

    def _build_column_tab(self, nb, title, group):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text=title)
        ttk.Label(f, text="双击“显示”单元格可切换显示/隐藏；选中一行可改宽度和上下移动。").pack(anchor="w")
        tree = ttk.Treeview(f, columns=("key", "title", "width", "show"),
                            show="headings", height=10)
        for key, head, w in (("key", "字段", 110), ("title", "列名", 120),
                             ("width", "宽度", 70), ("show", "显示", 60)):
            tree.heading(key, text=head)
            tree.column(key, width=w, anchor="w")
        tree.pack(fill="both", expand=True, pady=6)
        tree.bind("<Double-1>", lambda e, t=tree: self._toggle_column(e, t))
        tree.bind("<<TreeviewSelect>>", lambda _e, t=tree, g=group: self._col_selected(t, g))

        bar = ttk.Frame(f); bar.pack(fill="x")
        ttk.Button(bar, text="↑ 上移", command=lambda: self._move_tree(tree, group, -1)).pack(side="left", padx=3)
        ttk.Button(bar, text="↓ 下移", command=lambda: self._move_tree(tree, group, 1)).pack(side="left", padx=3)
        ttk.Button(bar, text="显示/隐藏", command=lambda: self._toggle_selected(tree)).pack(side="left", padx=3)
        ttk.Label(bar, text="宽度:").pack(side="left", padx=(14, 3))
        self.__dict__[f"width_var_{group}"] = tk.StringVar(value="80")
        sp = ttk.Spinbox(bar, from_=30, to=600, width=7,
                         textvariable=self.__dict__[f"width_var_{group}"],
                         command=lambda t=tree, g=group: self._set_width(t, g))
        sp.pack(side="left")
        ttk.Button(bar, text="应用宽度", command=lambda t=tree, g=group: self._set_width(t, g)).pack(side="left", padx=3)
        return tree

    # --------------------------------------------------------- 数据同步
    def _load_into_widgets(self):
        self.tab_list.delete(0, "end")
        for name in self.data.get("tab_order", []):
            self.tab_list.insert("end", name)
        for group, tree in (("lib_columns", self.lib_view),
                            ("queue_columns", self.queue_view)):
            tree.delete(*tree.get_children())
            for col in self.data.get(group, []):
                tree.insert("", "end", iid=col["key"],
                            values=(col["key"], col["title"], col["width"],
                                    "是" if col.get("visible", True) else "否"))
        self.redraw()

    def collect(self) -> dict:
        data = copy.deepcopy(layout_cfg.DEFAULT_LAYOUT)
        data["explorer_mode"] = self.mode.get()
        data["auto_refresh_on_connect"] = self.auto_refresh.get()
        data["show_progress_dialog"] = self.show_progress.get()
        data["show_lib_search"] = self.show_search.get()
        for key, var in self.sash_vars.items():
            data["sash"][key] = var.get()
        data["tab_order"] = list(self.tab_list.get(0, "end"))
        for group, tree in (("lib_columns", self.lib_view),
                            ("queue_columns", self.queue_view)):
            cols = []
            for iid in tree.get_children():
                vals = tree.item(iid, "values")
                cols.append({"key": vals[0], "title": vals[1],
                             "width": int(vals[2]), "visible": vals[3] == "是"})
            data[group] = cols
        return layout_cfg._merge_default(data)

    def redraw(self):
        """在右侧画一张布局示意图。"""
        c = self.canvas
        c.delete("all")
        w, h = max(400, c.winfo_width()), max(300, c.winfo_height())
        pad = 18
        main_top = pad + 26
        main_h = h - main_top - 58
        d = self.collect()
        left_w = int((w - 2 * pad) * d["sash"]["main_explorer"])

        c.create_text(pad, pad, anchor="nw", text="主窗口", fill="#333",
                      font=("Microsoft YaHei UI", 10, "bold"))
        c.create_rectangle(pad, main_top, w - pad, main_top + main_h,
                           outline="#888", fill="#FFFFFF")
        c.create_rectangle(pad, main_top, pad + left_w, main_top + main_h,
                           outline="#4A90D9", fill="#EAF3FD", width=2)
        c.create_text(pad + 8, main_top + 8, anchor="nw",
                      text="资源管理器\n(原生 Explorer)" if self.mode.get() == "native"
                           else "资源管理器\n(内置树)",
                      fill="#245", font=("Microsoft YaHei UI", 10))
        nav_w = int(left_w * 0.35)
        c.create_rectangle(pad + 6, main_top + 52, pad + nav_w, main_top + main_h - 8,
                           outline="#7AB", fill="#D9EAFB")
        c.create_text(pad + 12, main_top + 58, anchor="nw", text="左侧导航树", fill="#245")
        c.create_rectangle(pad + nav_w + 6, main_top + 52, pad + left_w - 6,
                           main_top + main_h - 8, outline="#7AB", fill="#F4F9FF")
        c.create_text(pad + nav_w + 12, main_top + 58, anchor="nw",
                      text="右侧详细文件栏", fill="#245")

        rx = pad + left_w + 8
        rw = w - pad - rx
        tab_x = rx
        for name in d["tab_order"]:
            tw = max(70, rw // 3 - 4)
            c.create_rectangle(tab_x, main_top + 2, tab_x + tw, main_top + 28,
                               outline="#999", fill="#EEE")
            c.create_text(tab_x + 4, main_top + 15, anchor="w", text=name, fill="#333")
            tab_x += tw + 4
        c.create_rectangle(rx, main_top + 34, w - pad, main_top + main_h,
                           outline="#999", fill="#FCFCFC")

        # 机器歌曲页预览
        lib_w = int(rw * d["sash"]["machine_lib"])
        c.create_rectangle(rx + 6, main_top + 44, rx + lib_w, main_top + main_h - 8,
                           outline="#5A5", fill="#F1FAF1")
        y = main_top + 50
        if self.show_search.get():
            c.create_rectangle(rx + 12, y, rx + lib_w - 12, y + 22,
                               outline="#8B8", fill="#E4F5E4")
            c.create_text(rx + 18, y + 11, anchor="w", text="搜索定位", fill="#264")
            y += 30
        cols = [x["title"] for x in d["lib_columns"] if x.get("visible")]
        cw = max(30, (lib_w - 20) // max(1, len(cols)))
        x = rx + 12
        for name in cols[:8]:
            c.create_rectangle(x, y, x + cw - 2, y + 20, outline="#AAA", fill="#FFF")
            c.create_text(x + 3, y + 10, anchor="w", text=name[:4], fill="#444")
            x += cw
        c.create_line(rx + lib_w - 6, main_top + 44, rx + lib_w - 6,
                      main_top + main_h - 8, fill="#555", width=2)
        c.create_rectangle(rx + lib_w + 2, main_top + 44, w - pad - 8,
                           main_top + main_h - 8, outline="#A7A", fill="#FBF4FB")
        c.create_text(rx + lib_w + 10, main_top + 52, anchor="nw",
                      text="播放列表 + 列表内曲目", fill="#535")

        c.create_text(pad, h - 34, anchor="nw",
                      text="提示：这里调整的是主程序布局，不会改动播放器里的歌曲。",
                      fill="#777")

    # ----------------------------------------------------------- 操作
    @staticmethod
    def _move_list(lb, delta):
        idx = lb.curselection()
        if not idx:
            return
        i = idx[0]
        j = i + delta
        if j < 0 or j >= lb.size():
            return
        items = list(lb.get(0, "end"))
        items[i], items[j] = items[j], items[i]
        lb.delete(0, "end")
        for x in items:
            lb.insert("end", x)
        lb.selection_clear(0, "end")
        lb.selection_set(j)
        lb.activate(j)

    def _move_tree(self, tree, group, delta):
        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]
        items = list(tree.get_children())
        i = items.index(iid)
        j = i + delta
        if j < 0 or j >= len(items):
            return
        items[i], items[j] = items[j], items[i]
        cached = {k: tree.item(k, "values") for k in items}
        tree.delete(*tree.get_children())
        for k in items:
            tree.insert("", "end", iid=k, values=cached[k])
        tree.selection_set(iid)

    def _toggle_column(self, event, tree):
        col = tree.identify_column(event.x)
        iid = tree.identify_row(event.y)
        if not iid:
            return
        if col == "#4":
            self._toggle_one(tree, iid)

    def _toggle_selected(self, tree):
        sel = tree.selection()
        if sel:
            self._toggle_one(tree, sel[0])

    def _toggle_one(self, tree, iid):
        vals = list(tree.item(iid, "values"))
        if vals[0] in ("num", "#"):
            messagebox.showinfo("提示", "固定序号列不能隐藏。")
            return
        vals[3] = "否" if vals[3] == "是" else "是"
        tree.item(iid, values=vals)
        self.redraw()

    def _col_selected(self, tree, group):
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        self.__dict__[f"width_var_{group}"].set(str(vals[2]))

    def _set_width(self, tree, group):
        sel = tree.selection()
        if not sel:
            return
        try:
            width = int(self.__dict__[f"width_var_{group}"].get())
        except Exception:
            return
        vals = list(tree.item(sel[0], "values"))
        vals[2] = width
        tree.item(sel[0], values=vals)
        self.redraw()

    def save_layout(self):
        data = self.collect()
        try:
            p = layout_cfg.save_layout(data, str(self.path))
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.path_var.set(str(p))
        messagebox.showinfo("已保存", f"布局已保存到：\n{p}\n\n请重启 njb3_gui.exe 生效。")

    def open_layout(self):
        p = filedialog.askopenfilename(title="打开布局 JSON",
                                       filetypes=(("JSON", "*.json"), ("所有文件", "*.*")))
        if not p:
            return
        try:
            raw = json.loads(Path(p).read_text(encoding="utf-8"))
            self.data = layout_cfg._merge_default(raw)
            self.mode.set(self.data["explorer_mode"])
            self.auto_refresh.set(self.data["auto_refresh_on_connect"])
            self.show_progress.set(self.data["show_progress_dialog"])
            self.show_search.set(self.data["show_lib_search"])
            for k, v in self.data["sash"].items():
                self.sash_vars[k].set(v)
            self._load_into_widgets()
            self.path = Path(p)
            self.path_var.set(str(self.path))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def reset_default(self):
        if not messagebox.askyesno("恢复默认", "把所有设置恢复为默认布局？"):
            return
        self.data = layout_cfg.default_layout()
        self.mode.set(self.data["explorer_mode"])
        self.auto_refresh.set(True)
        self.show_progress.set(True)
        self.show_search.set(True)
        for k, v in self.data["sash"].items():
            self.sash_vars[k].set(v)
        self._load_into_widgets()


def probe(root, editor):
    import json
    out = Path(os.path.dirname(os.path.abspath(sys.executable
                                               if getattr(sys, "frozen", False)
                                               else __file__))) / "_gui_editor_probe.json"
    info = {
        "ok": True,
        "tabs": list(editor.tab_list.get(0, "end")),
        "lib_columns": len(editor.collect()["lib_columns"]),
        "queue_columns": len(editor.collect()["queue_columns"]),
        "has_preview": editor.canvas.winfo_exists() == 1,
    }
    out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    root.after(200, root.destroy)


def main():
    root = tk.Tk()
    editor = LayoutEditor(root)
    if "--probe" in sys.argv:
        root.after(300, lambda: probe(root, editor))
    root.mainloop()


if __name__ == "__main__":
    main()
