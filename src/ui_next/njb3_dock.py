#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_dock.py — 纯 tkinter 停靠工作区引擎（Premiere / Resolume 风格）

架构要点（重要）：
  tkinter 的 pack/place(in_=X) 要求 X 必须是控件 master 的后代，无法把控件
  在兄弟容器间移动。因此本引擎让【所有面板 outer 都创建在同一个 stage 容器】，
  用 place() 按布局树计算绝对像素坐标来摆放。停靠重排 = 重新计算坐标，
  从不销毁/重建业务控件 → 曲库、播放、传输等状态 100% 保留。

能力：
  · 每个面板有持久标题栏，按住拖动。
  · 拖到另一面板【中心】→ 合并页签；拖到【上/下/左/右】→ 自动贴合分割。
  · 拖到空白处 → 变成可自由摆放的浮动面板（右下角可拉伸尺寸）。
  · 分割条可拖动调比例；页签可点击切换、可拖出拆分。
  · 布局树序列化到 JSON，重启恢复；窗口缩放自动重排。
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import messagebox

DRAG_THRESHOLD = 6
SASH = 4          # v39：分割条变细(px)，原 8 太宽像黑条
TABH = 28         # 页签栏高度(px)
MIN_RATIO = 0.10


def _inside(x, y, rect):
    rx, ry, rw, rh = rect
    return rx <= x <= rx + rw and ry <= y <= ry + rh


class DockPanel:
    """持久面板：outer(master=stage) = header(标题栏) + body(业务容器)。"""

    def __init__(self, mgr, pid, title, builder):
        self.mgr = mgr
        self.id = pid
        self.title = title
        self.show_header = getattr(mgr, "show_header", True)
        self.slim = getattr(mgr, "slim", False)
        self.show_buttons = getattr(mgr, "show_panel_buttons", True)
        self.allow_menu = getattr(mgr, "allow_panel_menu", True)
        self.outer = tk.Frame(mgr.stage, bg=mgr.pal["panel"],
                              highlightbackground=mgr.pal["border"],
                              highlightthickness=1, bd=0)
        self.header = None
        self.grip = None
        self.btn_float = None
        self.btn_menu = None
        if self.show_header:
            hh = 20 if self.slim else 25
            fs = 8 if self.slim else 9
            self.header = tk.Frame(self.outer, bg=mgr.pal["header"], height=hh)
            self.body = tk.Frame(self.outer, bg=mgr.pal["panel"])
            self.header.pack(fill="x", side="top")
            self.body.pack(fill="both", expand=True, side="top")

            self.grip = tk.Label(self.header, text="⠿  " + title, bg=mgr.pal["header"],
                                 fg=mgr.pal["header_fg"],
                                 font=("Microsoft YaHei UI", fs, "bold"), anchor="w")
            self.grip.pack(side="left", fill="x", expand=True, padx=(6, 2))
            if self.show_buttons:
                self.btn_float = tk.Button(self.header, text="▣", width=2, bd=0,
                                           bg=mgr.pal["header"], fg=mgr.pal["header_fg"],
                                           activebackground=mgr.pal["tab_active"],
                                           command=self.toggle_float)
                self.btn_float.pack(side="right", padx=2, pady=1)
                self.btn_menu = tk.Button(self.header, text="⋮", width=2, bd=0,
                                          bg=mgr.pal["header"], fg=mgr.pal["header_fg"],
                                          activebackground=mgr.pal["tab_active"],
                                          command=self._show_menu)
                self.btn_menu.pack(side="right", padx=2, pady=1)
        else:
            # 整页模式：无标题栏（无“眉头”），业务容器直接铺满。
            self.body = tk.Frame(self.outer, bg=mgr.pal["panel"])
            self.body.pack(fill="both", expand=True)

        try:
            builder(self.body)
        except Exception as e:
            tk.Label(self.body, text=f"[面板构建失败] {e}", fg="#e66",
                     bg=mgr.pal["panel"]).pack(padx=8, pady=8)

        for w in (self.header, self.grip):
            if w is None:
                continue
            w.bind("<ButtonPress-1>", lambda e, p=self: p._press(e))
            w.bind("<B1-Motion>", lambda e, p=self: p._motion(e))
            w.bind("<ButtonRelease-1>", lambda e, p=self: p._release(e))
            if self.allow_menu:
                w.bind("<Button-3>", lambda e, p=self: p._show_menu(e))
        self._press_info = None
        self.dragging = False
        self.rect = (0, 0, 1, 1)

    def _press(self, e):
        self._press_info = (e.x_root, e.y_root)
        self.dragging = False

    def _motion(self, e):
        if not self._press_info:
            return
        x0, y0 = self._press_info
        if not self.dragging and abs(e.x_root - x0) + abs(e.y_root - y0) < DRAG_THRESHOLD:
            return
        if not self.dragging:
            self.dragging = True
            self.mgr.begin_drag(self, e.x_root, e.y_root)
        self.mgr.update_drag(self, e.x_root, e.y_root)

    def _release(self, e):
        if self._press_info and self.dragging:
            self.mgr.end_drag(self, e.x_root, e.y_root)
        self._press_info = None
        self.dragging = False

    def _show_menu(self, e=None):
        if not self.allow_menu:
            return
        m = tk.Menu(self.outer, tearoff=0)
        m.add_command(label="合并到中心页签", command=lambda: self.mgr.dock_to(self.id, None, "center"))
        m.add_separator()
        for z, t in (("left", "停靠到左侧"), ("right", "停靠到右侧"),
                     ("top", "停靠到上方"), ("bottom", "停靠到下方")):
            m.add_command(label=t, command=lambda zz=z: self.mgr.dock_to(self.id, None, zz))
        m.add_separator()
        m.add_command(label="浮动摆放", command=lambda: self.mgr.to_float(self.id))
        m.add_command(label="重置默认布局", command=self.mgr.reset_layout)
        try:
            if e is not None:
                m.tk_popup(e.x_root, e.y_root)
            elif self.btn_menu is not None:
                m.tk_popup(self.btn_menu.winfo_rootx(), self.btn_menu.winfo_rooty() + 20)
        finally:
            m.grab_release()

    def toggle_float(self):
        if self.id in self.mgr.floats:
            self.mgr.dock_to(self.id, None, "center")
        else:
            self.mgr.to_float(self.id)


class DockManager:
    def __init__(self, stage, pal, config_path=None, on_relayout=None,
                 default_layout=None, show_header=True, slim=False,
                 show_panel_buttons=True, allow_panel_menu=True):
        self.stage = stage
        self.pal = pal
        self.config_path = config_path
        self.on_relayout = on_relayout
        self.default_layout = default_layout
        self.show_header = show_header
        self.slim = slim
        self.show_panel_buttons = show_panel_buttons
        self.allow_panel_menu = allow_panel_menu
        self.panels = {}
        self.tree = None
        self.floats = {}
        self.hidden = set()
        self._ghost = None
        self._ind = None
        self._drag_info = None
        self._sash_drag = None
        self._float_grips = {}
        self._tab_frames = []
        self._sash_frames = []
        self._node_rects = {}
        self._last_size = (0, 0)
        stage.bind("<Configure>", lambda e: self._on_stage_configure())

    def add_panel(self, pid, title, builder):
        self.panels[pid] = DockPanel(self, pid, title, builder)
        return self.panels[pid]

    # ------------------------------------------------------------ 默认布局
    def default_tree(self, ids):
        ids = [i for i in ids if i in self.panels]
        if not ids:
            return {"type": "tabs", "panels": [], "active": None}
        explorer, rest = ids[0], ids[1:]
        right = {"type": "tabs", "panels": rest, "active": rest[0] if rest else None}
        return {"type": "paned", "orient": "horizontal",
                "children": [
                    {"type": "tabs", "panels": [explorer], "active": explorer},
                    right,
                ],
                "ratios": [0.25, 0.75]}

    def load_or_default(self, ids, defaults=None):
        self.default_ids = list(ids)
        ok = False
        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") == 4 and self._validate(data):
                    self.tree, self.floats = data["tree"], data.get("floats", {})
                    self.hidden = set(data.get("hidden", []))
                    ok = True
            except Exception:
                ok = False
        if not ok:
            self.tree = defaults or self.default_tree(ids)
            self.floats, self.hidden = {}, set()
        self.relayout()

    def _validate(self, data):
        try:
            known = set(self.panels)

            def walk(n):
                if not isinstance(n, dict):
                    return False
                t = n.get("type")
                if t == "paned":
                    return n.get("children") and all(walk(c) for c in n["children"])
                if t == "tabs":
                    return all(p in known for p in n.get("panels", []))
                return False
            return "tree" in data and walk(data["tree"])
        except Exception:
            return False

    def save(self):
        if not self.config_path:
            return
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump({"version": 4, "tree": self.tree, "floats": self.floats,
                           "hidden": sorted(self.hidden)}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def reset_layout(self):
        if messagebox.askyesno("重置布局", "恢复默认面板布局？当前摆放会被覆盖。"):
            ids = getattr(self, "default_ids", list(self.panels))
            factory = self.default_layout or self.default_tree
            self.tree = factory(ids)
            self.floats, self.hidden = {}, set()
            self.save()
            self.relayout()

    # ------------------------------------------------------------ 布局计算
    def _on_stage_configure(self):
        size = (self.stage.winfo_width(), self.stage.winfo_height())
        if size != self._last_size and size[0] > 30 and size[1] > 30:
            self._last_size = size
            self.relayout()

    def relayout(self):
        W, H = self.stage.winfo_width(), self.stage.winfo_height()
        if W < 30 or H < 30:
            W, H = 1000, 620
        for f in self._tab_frames + self._sash_frames:
            try:
                f.destroy()
            except Exception:
                pass
        self._tab_frames, self._sash_frames = [], []
        self._node_rects = {}
        for p in self.panels.values():
            p.outer.place_forget()
        if self.tree:
            self._layout_node(self.tree, 0, 0, W, H)
        for pid, geom in self.floats.items():
            p = self.panels.get(pid)
            if p:
                gx = max(0, min(int(geom.get("x", 60)), W - 80))
                gy = max(0, min(int(geom.get("y", 60)), H - 40))
                gw = max(200, min(int(geom.get("w", 420)), W - gx))
                gh = max(140, min(int(geom.get("h", 300)), H - gy))
                p.rect = (gx, gy, gw, gh)
                p.outer.place(x=gx, y=gy, width=gw, height=gh)
                p.outer.lift()
                self._mount_float_grip(p)
        if self.on_relayout:
            try:
                self.on_relayout()
            except Exception:
                pass

    def _spans(self, node, total):
        """按当前比例算出各子块像素尺寸，并施加 min_px / max_px / max_ratios 约束。"""
        children = [c for c in node.get("children", []) if self._has_panels(c)]
        n = len(children)
        if not n:
            return []
        avail = max(1, total - SASH * (n - 1))
        ratios = node.get("ratios") or [1.0 / n] * n
        if len(ratios) != n:
            ratios = [1.0 / n] * n
        spans = [int(avail * ratios[i]) for i in range(n - 1)]
        spans.append(avail - sum(spans))

        mins = node.get("min_px") or [0] * n
        if len(mins) != n:
            mins = [0] * n
        mins = [max(0.0, min(float(avail), float(m))) for m in mins]
        maxs = []
        max_px = node.get("max_px") or []
        max_ratios = node.get("max_ratios") or []
        for i in range(n):
            limit = float(avail)
            if i < len(max_px) and max_px[i] is not None:
                limit = min(limit, float(max_px[i]))
            if i < len(max_ratios) and max_ratios[i] is not None:
                limit = min(limit, avail * float(max_ratios[i]))
            maxs.append(max(mins[i], min(float(avail), limit)))

        for i in range(n):
            spans[i] = max(spans[i], int(mins[i]))
        for i in range(n - 1, -1, -1):
            spans[i] = min(spans[i], int(maxs[i]))
        diff = avail - sum(spans)
        for _pass in range(3):
            if not diff:
                break
            idxs = range(n) if diff > 0 else range(n - 1, -1, -1)
            for i in idxs:
                if not diff:
                    break
                if diff > 0:
                    add = min(int(maxs[i]) - spans[i], diff)
                else:
                    add = -min(spans[i] - int(mins[i]), -diff)
                if add:
                    spans[i] += add
                    diff -= add
        if diff:
            spans[0] += diff
        node["ratios"] = [max(0.0, s / avail) for s in spans]
        return spans

    def apply_content_mins(self, cap_h=170, floor_h=68, cap_w=240, floor_w=120):
        """根据控件实际 reqsize 推导每个分割节点的最小尺寸，防止把内容拖没。

        · 垂直分割：子块最小高度取内容高度（上限 cap_h）。
        · 水平分割：子块最小宽度取内容宽度（上限 cap_w）。
        结果写进节点 min_px，拖动与重排都会遵守。
        """
        def leaf_h(pid):
            panel = self.panels.get(pid)
            if not panel:
                return floor_h
            h = panel.body.winfo_reqheight()
            if panel.header is not None:
                try:
                    h += panel.header.winfo_reqheight()
                except Exception:
                    h += 20
            return max(floor_h, min(cap_h, h + 12))

        def leaf_w(pid):
            panel = self.panels.get(pid)
            if not panel:
                return floor_w
            return max(floor_w, min(cap_w, panel.body.winfo_reqwidth()))

        def h_min(node):
            if not node:
                return floor_h
            if node.get("type") == "tabs":
                ids = [p for p in node.get("panels", []) if p in self.panels]
                return max((leaf_h(p) for p in ids), default=floor_h)
            if node.get("type") == "paned":
                kids = [c for c in node.get("children", []) if self._has_panels(c)]
                if not kids:
                    return floor_h
                vals = [h_min(c) for c in kids]
                if node.get("orient") == "vertical":
                    return sum(vals) + SASH * (len(vals) - 1)
                return max(vals)
            return floor_h

        def w_min(node):
            if not node:
                return floor_w
            if node.get("type") == "tabs":
                ids = [p for p in node.get("panels", []) if p in self.panels]
                return max((leaf_w(p) for p in ids), default=floor_w)
            if node.get("type") == "paned":
                kids = [c for c in node.get("children", []) if self._has_panels(c)]
                if not kids:
                    return floor_w
                vals = [w_min(c) for c in kids]
                if node.get("orient") == "horizontal":
                    return sum(vals) + SASH * (len(vals) - 1)
                return max(vals)
            return floor_w

        def assign(node):
            if not node or node.get("type") != "paned":
                return
            kids = [c for c in node.get("children", []) if self._has_panels(c)]
            if not kids:
                return
            if node.get("orient") == "vertical":
                node["min_px"] = [h_min(c) for c in kids]
            else:
                node["min_px"] = [w_min(c) for c in kids]
            for c in kids:
                assign(c)

        assign(self.tree)
        self.relayout()

    def set_panel_min_heights(self, heights):
        """为指定面板设置最小高度（垂直分割节点自动累加）。"""
        def h_min(node):
            if not node:
                return 68
            if node.get("type") == "tabs":
                ids = [p for p in node.get("panels", []) if p in self.panels]
                return max((heights.get(p, 68) for p in ids), default=68)
            if node.get("type") == "paned":
                kids = [c for c in node.get("children", []) if self._has_panels(c)]
                if not kids:
                    return 68
                vals = [h_min(c) for c in kids]
                if node.get("orient") == "vertical":
                    return sum(vals) + SASH * (len(vals) - 1)
                return max(vals)
            return 68

        def assign(node):
            if not node or node.get("type") != "paned":
                return
            kids = [c for c in node.get("children", []) if self._has_panels(c)]
            if not kids:
                return
            if node.get("orient") == "vertical":
                node["min_px"] = [h_min(c) for c in kids]
            for c in kids:
                assign(c)

        assign(self.tree)
        self.relayout()

    def _layout_node(self, node, x, y, w, h):
        if w <= 0 or h <= 0:
            return
        self._node_rects[id(node)] = (x, y, w, h)
        t = node.get("type")
        if t == "paned":
            children = [c for c in node.get("children", []) if self._has_panels(c)]
            if not children:
                return
            spans = self._spans(node, w if node.get("orient") == "horizontal" else h)
            pos = 0
            for i, ch in enumerate(children):
                span = spans[i]
                if node.get("orient") == "horizontal":
                    self._layout_node(ch, x + pos, y, span, h)
                    pos += span
                    if i < len(children) - 1:
                        self._make_sash(node, i, x + pos - SASH // 2, y, SASH, h)
                        pos += SASH
                else:
                    self._layout_node(ch, x, y + pos, w, span)
                    pos += span
                    if i < len(children) - 1:
                        self._make_sash(node, i, x, y + pos - SASH // 2, w, SASH)
                        pos += SASH
        elif t == "tabs":
            panels = [pid for pid in node.get("panels", []) if pid in self.panels]
            if not panels:
                return
            body_y, body_h = y, h
            if len(panels) > 1 and node.get("show_tabs", True):
                self._make_tabbar(node, panels, x, y, w)
                body_y, body_h = y + TABH, max(1, h - TABH)
            active = node.get("active") if node.get("active") in panels else panels[0]
            node["active"] = active
            for pid in panels:
                p = self.panels[pid]
                if pid == active:
                    p.rect = (x, body_y, w, body_h)
                    p.outer.place(x=x, y=body_y, width=w, height=body_h)
                else:
                    p.outer.place_forget()

    def _make_tabbar(self, node, panels, x, y, w):
        bar = tk.Frame(self.stage, bg=self.pal["header"], height=TABH)
        bar.place(x=x, y=y, width=w, height=TABH)
        self._tab_frames.append(bar)
        for pid in panels:
            p = self.panels[pid]
            active = pid == node.get("active")
            lab = tk.Label(bar, text=p.title, padx=10,
                           bg=self.pal["tab_active"] if active else self.pal["tab_bg"],
                           fg=self.pal["text"] if active else self.pal["text_dim"],
                           font=("Microsoft YaHei UI", 9, "bold" if active else "normal"))
            lab.pack(side="left", fill="y")
            lab.bind("<ButtonPress-1>", lambda e, pp=pid: self._tab_press(e, pp))
            lab.bind("<B1-Motion>", lambda e, pp=pid: self._tab_motion(e, pp))
            lab.bind("<ButtonRelease-1>", lambda e, pp=pid: self._tab_release(e, pp))

    def _tab_press(self, e, pid):
        self._tab_drag = (pid, e.x_root, e.y_root, False)

    def _tab_motion(self, e, pid):
        d = getattr(self, "_tab_drag", None)
        if not d or d[0] != pid:
            return
        _, x0, y0, started = d
        if not started and abs(e.x_root - x0) + abs(e.y_root - y0) < DRAG_THRESHOLD:
            return
        if not started:
            self._tab_drag = (pid, x0, y0, True)
            self.begin_drag(self.panels[pid], e.x_root, e.y_root)
        self.update_drag(self.panels[pid], e.x_root, e.y_root)

    def _tab_release(self, e, pid):
        d = getattr(self, "_tab_drag", None)
        if d and d[0] == pid and d[3]:
            self._tab_drag = None
            self.end_drag(self.panels[pid], e.x_root, e.y_root)
        else:
            self._tab_drag = None
            tabs = self._find_tabs_of(pid)
            if tabs:
                tabs["active"] = pid
                self.save()
                self.relayout()

    def _make_sash(self, node, index, x, y, sw, sh):
        vertical = node.get("orient") == "vertical"
        # vertical 分割条是水平长条 → 上下拖 → 上下双箭头；
        # horizontal 分割条是竖直长条 → 左右拖 → 左右双箭头（原来错用 sizing）。
        sash = tk.Frame(self.stage, bg=self.pal.get("sash", self.pal["border"]),
                        cursor="sb_v_double_arrow" if vertical else "sb_h_double_arrow")
        sash.place(x=x, y=y, width=sw, height=sh)
        sash.bind("<ButtonPress-1>",
                  lambda e, n=node, i=index: self._sash_press(e, n, i))
        self._sash_frames.append(sash)

    def _sash_press(self, e, node, index):
        # 关键修复：拖动期间不能让 relayout 销毁正在承载 B1-Motion 绑定的 sash，
        # 否则鼠标一移出就被销毁，出现“只能拖一点”。改为把 motion/release 绑到
        # 根窗口，用全局事件跟踪，sash 可随 relayout 安全重建。
        self._sash_drag = (node, index, e.x_root, e.y_root, list(node["ratios"]))
        self.stage.bind_all("<B1-Motion>", self._sash_motion)
        self.stage.bind_all("<ButtonRelease-1>", lambda ev: self._sash_release())

    def _sash_motion(self, e):
        d = self._sash_drag
        if not d:
            return
        node, i, x0, y0, base = d
        rect = self._node_rects.get(id(node))
        if not rect:
            return
        vertical = node.get("orient") == "vertical"
        total = rect[3] if vertical else rect[2]
        kids = [c for c in node.get("children", []) if self._has_panels(c)]
        n = len(kids)
        if n < 2 or i + 1 >= n:
            return
        avail = max(1, total - SASH * (n - 1))
        delta = (e.y_root - y0) if vertical else (e.x_root - x0)

        r = list(base)
        if len(r) != n:
            r = [1.0 / n] * n
        s = [int(avail * r[k]) for k in range(n - 1)]
        s.append(avail - sum(s))
        pair = s[i] + s[i + 1]

        mins = node.get("min_px") or [0] * n
        if len(mins) != n:
            mins = [0] * n
        lo = max(0.0, float(mins[i]))
        hi = pair - max(0.0, float(mins[i + 1]))
        max_px = node.get("max_px") or []
        max_ratios = node.get("max_ratios") or []
        for k, limit in ((i, max_px[i] if i < len(max_px) else None),
                         (i + 1, max_px[i + 1] if i + 1 < len(max_px) else None)):
            if limit is not None:
                if k == i:
                    hi = min(hi, float(limit))
                else:
                    lo = max(lo, pair - float(limit))
        for k, limit in ((i, max_ratios[i] if i < len(max_ratios) else None),
                         (i + 1, max_ratios[i + 1] if i + 1 < len(max_ratios) else None)):
            if limit is not None:
                if k == i:
                    hi = min(hi, avail * float(limit))
                else:
                    lo = max(lo, pair - avail * float(limit))
        lo = max(0.0, min(float(pair), lo))
        hi = max(lo, min(float(pair), hi))
        new_i = max(lo, min(hi, s[i] + delta))
        r[i] = new_i / avail
        r[i + 1] = (pair - new_i) / avail
        node["ratios"] = r
        self.relayout()

    def _sash_release(self):
        if self._sash_drag:
            self._sash_drag = None
            try:
                self.stage.unbind_all("<B1-Motion>")
                self.stage.unbind_all("<ButtonRelease-1>")
            except Exception:
                pass
            self.save()

    def _mount_float_grip(self, panel):
        grip = self._float_grips.get(panel.id)
        if grip is None:
            grip = tk.Frame(panel.outer, bg=self.pal["accent"], cursor="bottom_right_corner")
            grip.bind("<ButtonPress-1>", lambda e, p=panel: self._rsz_press(e, p))
            grip.bind("<B1-Motion>", self._rsz_motion)
            grip.bind("<ButtonRelease-1>", self._rsz_release)
            self._float_grips[panel.id] = grip
        grip.place(relx=1.0, rely=1.0, x=-14, y=-14, width=14, height=14)

    def _rsz_press(self, e, panel):
        self._rsz = (panel, e.x_root, e.y_root, panel.rect[2], panel.rect[3])

    def _rsz_motion(self, e):
        d = getattr(self, "_rsz", None)
        if not d:
            return
        panel, x0, y0, w0, h0 = d
        w = max(200, w0 + e.x_root - x0)
        h = max(140, h0 + e.y_root - y0)
        g = self.floats.setdefault(panel.id, {"x": panel.rect[0], "y": panel.rect[1]})
        g.update({"w": w, "h": h})
        panel.rect = (panel.rect[0], panel.rect[1], w, h)
        panel.outer.place(width=w, height=h)

    def _rsz_release(self, e):
        if getattr(self, "_rsz", None):
            self._rsz = None
            self.save()

    # ------------------------------------------------------------ 树操作
    def _has_panels(self, node):
        if not node:
            return False
        if node.get("type") == "tabs":
            return any(p in self.panels for p in node.get("panels", []))
        return any(self._has_panels(c) for c in node.get("children", []))

    def _find_tabs_of(self, pid, node=None):
        node = node if node is not None else self.tree
        if not node:
            return None
        if node.get("type") == "tabs":
            return node if pid in node.get("panels", []) else None
        for ch in node.get("children", []):
            r = self._find_tabs_of(pid, ch)
            if r:
                return r
        return None

    def find_panel_parent(self, pid, node=None):
        """返回 (paned_node, child_index)：面板所在的直接水平/垂直分割节点。"""
        node = node if node is not None else self.tree
        if not node:
            return None, None
        if node.get("type") == "paned":
            for i, ch in enumerate(node.get("children", [])):
                if ch and ch.get("type") == "tabs" and pid in ch.get("panels", []):
                    return node, i
                found = self.find_panel_parent(pid, ch)
                if found[0] is not None:
                    return found
        return None, None

    def set_node_ratios(self, node, ratios, relayout=True):
        """直接设置某个分割节点的比例并持久化。"""
        if not node or node.get("type") != "paned":
            return
        node["ratios"] = list(ratios)
        self.save()
        if relayout:
            self.relayout()

    def _detach(self, pid):
        self.floats.pop(pid, None)
        self.hidden.discard(pid)
        tabs = self._find_tabs_of(pid)
        if tabs and pid in tabs["panels"]:
            tabs["panels"].remove(pid)
            if tabs["active"] == pid:
                tabs["active"] = tabs["panels"][0] if tabs["panels"] else None
        self._prune(self.tree)

    def _prune(self, node):
        if not node or node.get("type") != "paned":
            return
        node["children"] = [c for c in node.get("children", []) if self._has_panels(c)]
        if node["children"]:
            n = len(node["children"])
            node["ratios"] = [1.0 / n] * n
        for c in node["children"]:
            self._prune(c)

    def _replace_target_with_split(self, target, src, zone):
        orient = "vertical" if zone in ("top", "bottom") else "horizontal"
        first = zone in ("left", "top")
        src_leaf = {"type": "tabs", "panels": [src], "active": src}

        def split_of(target_tabs):
            kids = [src_leaf, target_tabs] if first else [target_tabs, src_leaf]
            return {"type": "paned", "orient": orient, "children": kids,
                    "ratios": [0.4, 0.6]}

        # 目标就是根 tabs
        if self.tree and self.tree.get("type") == "tabs" \
                and target in self.tree.get("panels", []):
            self.tree = split_of(self.tree)
            return

        def rec(node):
            if not node or node.get("type") != "paned":
                return False
            for i, ch in enumerate(node.get("children", [])):
                if not ch:
                    continue
                if ch.get("type") == "tabs" and target in ch.get("panels", []):
                    node["children"][i] = split_of(ch)
                    return True
                if rec(ch):
                    return True
            return False

        if not rec(self.tree):
            # 目标不在主树（异常情况）：构造独立叶子作为根
            t_leaf = {"type": "tabs", "panels": [target], "active": target}
            self.tree = split_of(t_leaf)

    def _append_root(self, pid):
        leaf = {"type": "tabs", "panels": [pid], "active": pid}
        if self.tree and self.tree.get("type") == "paned":
            self.tree.setdefault("children", []).append(leaf)
            n = len(self.tree["children"])
            self.tree["ratios"] = [1.0 / n] * n
        else:
            self.tree = {"type": "paned", "orient": "horizontal",
                         "children": ([self.tree, leaf] if self.tree else [leaf]),
                         "ratios": [0.7, 0.3] if self.tree else [1.0]}

    def dock_to(self, pid, target, zone):
        if pid not in self.panels:
            return
        self._detach(pid)
        if target and target != pid and zone == "center":
            tabs = self._find_tabs_of(target)
            if tabs:
                tabs["panels"].append(pid)
                tabs["active"] = pid
            else:
                self._append_root(pid)
        elif target and target != pid:
            self._replace_target_with_split(target, pid, zone)
        else:
            self._append_root(pid)
        self.save()
        self.relayout()

    def to_float(self, pid):
        if pid not in self.panels:
            return
        p = self.panels[pid]
        x, y, w, h = p.rect
        self._detach(pid)
        self.floats[pid] = {"x": max(0, x), "y": max(0, y), "w": w, "h": h}
        self.save()
        self.relayout()

    # ------------------------------------------------------------ 拖拽
    def begin_drag(self, panel, x, y):
        x, y = int(x), int(y)
        g = tk.Toplevel(self.stage)
        g.overrideredirect(True)
        g.attributes("-topmost", True)
        try:
            g.attributes("-alpha", 0.82)
        except Exception:
            pass
        tk.Label(g, text="⠿  " + panel.title, bg=self.pal["select_bg"], fg="#ffffff",
                 font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=6).pack()
        self._ghost = g
        self.update_drag(panel, x, y)

    def update_drag(self, panel, x, y):
        x, y = int(x), int(y)
        if self._ghost:
            self._ghost.geometry(f"+{x + 12}+{y + 12}")
        target, zone = self._hit(x, y, panel.id)
        self._drag_info = (panel, target, zone)
        self._draw_indicator(target, zone)

    def end_drag(self, panel, x, y):
        _, target, zone = self._drag_info or (panel, None, None)
        self._clear_drag()
        if target and zone:
            self.dock_to(panel.id, target, zone)
        else:
            sx = self.stage.winfo_rootx()
            sy = self.stage.winfo_rooty()
            self._detach(panel.id)
            self.floats[panel.id] = {
                "x": max(0, min(x - sx - 80, self.stage.winfo_width() - 220)),
                "y": max(0, min(y - sy - 20, self.stage.winfo_height() - 60)),
                "w": 420, "h": 300,
            }
            self.save()
            self.relayout()

    def _clear_drag(self):
        for attr in ("_ghost", "_ind"):
            w = getattr(self, attr, None)
            if w:
                try:
                    w.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._drag_info = None

    def _hit(self, x, y, exclude):
        # 传入的是屏幕坐标（x_root/y_root），p.rect 是 stage 相对坐标 → 需换算
        sx = self.stage.winfo_rootx()
        sy = self.stage.winfo_rooty()
        lx, ly = x - sx, y - sy
        for pid, p in self.panels.items():
            if pid == exclude:
                continue
            if not p.outer.winfo_ismapped():
                continue
            if _inside(lx, ly, p.rect):
                px, py, pw, ph = p.rect
                fx, fy = (lx - px) / max(1, pw), (ly - py) / max(1, ph)
                if 0.28 <= fx <= 0.72 and 0.28 <= fy <= 0.72:
                    return pid, "center"
                d = {"left": fx, "right": 1 - fx, "top": fy, "bottom": 1 - fy}
                return pid, min(d, key=d.get)
        return None, None

    def _draw_indicator(self, target, zone):
        if self._ind:
            try:
                self._ind.destroy()
            except Exception:
                pass
            self._ind = None
        if not target or target not in self.panels:
            return
        x, y, w, h = self.panels[target].rect
        ind = tk.Frame(self.stage, bg=self.pal["accent"],
                       highlightbackground=self.pal["accent_fg"], highlightthickness=1)
        if zone == "center":
            ind.place(x=x, y=y, width=w, height=h)
        elif zone == "left":
            ind.place(x=x, y=y, width=max(6, w // 3), height=h)
        elif zone == "right":
            ind.place(x=x + w - max(6, w // 3), y=y, width=max(6, w // 3), height=h)
        elif zone == "top":
            ind.place(x=x, y=y, width=w, height=max(6, h // 3))
        elif zone == "bottom":
            ind.place(x=x, y=y + h - max(6, h // 3), width=w, height=max(6, h // 3))
        ind.lift()
        self._ind = ind

    def focus_panel(self, pid):
        """兼容原 self.nb.select(self.tab_xxx)：激活指定面板。"""
        if pid not in self.panels:
            return
        tabs = self._find_tabs_of(pid)
        if tabs:
            tabs["active"] = pid
        else:
            self.floats.setdefault(pid, {"x": 80, "y": 60, "w": 480, "h": 320})
        self.save()
        self.relayout()

    def set_palette(self, pal):
        self.pal = pal
        for p in self.panels.values():
            p.outer.configure(bg=pal["panel"], highlightbackground=pal["border"])
            if p.header is not None:
                p.header.configure(bg=pal["header"])
                p.grip.configure(bg=pal["header"], fg=pal["header_fg"])
                for b in (p.btn_float, p.btn_menu):
                    if b is not None:
                        b.configure(bg=pal["header"], fg=pal["header_fg"],
                                    activebackground=pal["tab_active"])
            p.body.configure(bg=pal["panel"])
        self.relayout()
