#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_studio.py — NJB3 工作台（v32 独立新版 UI）

设计原则：
  · 不改源文件：本程序子类化 scripts/njb3_gui.py 的 App，覆盖且仅覆盖 _build_ui()，
    设备连接、曲库、播放、传输等 5000 行业务逻辑 100% 原样复用。
  · 三个整页工作台：传输工作台 / 播控工作台 / 控制工作台。整页无页签栏、无标题“眉头”，
    由顶栏三按钮切换。
  · 资源管理器与传输队列嵌入“传输工作台”框体左右两侧，各自可折叠缩进。
  · 控制工作台内部模块（设备状态/播放控制/驱动/刷新/外观/日志）为可拖动、可贴边、
    可调大小的小框体；日志上拖有上限，不会挤压上方模块。
  · 播控工作台左右侧栏折叠后只留一道竖灰线与箭头按钮。
  · 设计系统：njb3_theme 提供 4 套皮肤 + 跟随系统 + WCAG AA 对比度。
"""
from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = HERE.parent / "scripts"
for _p in (str(HERE), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import usb.core

import njb3_theme as theme
import njb3_dock as dockmod
import njb3_gui as base


class _SafePlaylistDevice:
    """设备代理：空/损坏播放清单读取超时按 0 首处理，不中断整次连接。

    v42：libnjb protocol3.c 证实“清单里所有曲目都被删掉”后设备会返回
    异常短响应，旧版直接把 USBError 抛给 _worker，触发反复自动重连。
    这里只在【读取播放清单成员】这一条只读路径兜底，写操作仍然照常报错。
    """
    def __init__(self, dev, app):
        object.__setattr__(self, "_dev", dev)
        object.__setattr__(self, "_app", app)

    def __getattr__(self, name):
        return getattr(self._dev, name)

    def get_playlist_tracks(self, playlistid):
        try:
            return self._dev.get_playlist_tracks(playlistid)
        except usb.core.USBError:
            try:
                name = next(
                    (p.get("name") for p in getattr(self._app, "playlists", [])
                     if p.get("playlistid") == playlistid), playlistid)
                self._app._log(
                    f"[!] 播放清单「{name}」成员读取超时，按空清单跳过（不中断连接）。")
            except Exception:
                pass
            return []

    def read_id(self):
        """序列号缓存到设备对象生命周期内，避免同一连接重复读。"""
        cached = getattr(self._dev, "_njb3_serial_raw", None)
        if cached is None:
            cached = self._dev.read_id()
            try:
                setattr(self._dev, "_njb3_serial_raw", cached)
            except Exception:
                pass
        return cached

    def list_tracks(self, extended=False):
        """v46：按设备序列号缓存扩展字段，首次完整读、后续快读。

        连接/刷新内部都调 list_tracks(extended=True)：
          * 有该序号缓存且未强制 → 快读 basic（3s），按 trackid 补回扩展字段；
          * 无缓存或强制标志 → 完整读（57s），抽取慢字段写缓存。
        读失败时不吞异常，交给 _worker 的既有重试/兜底流程。
        """
        app = self._app
        force = bool(getattr(app, "_force_full_library_read", False))
        try:
            serial = serial_key(self.read_id())
        except Exception as ex:
            # 序列号读不到：退回原样完整读，保证功能不退化。
            try:
                app._log(f"[!] 设备序列号读取失败，按完整曲库读取：{ex}")
            except Exception:
                pass
            serial = None

        cache = load_library_cache()
        entry = cache.get("devices", {}).get(serial) if serial else None
        use_cache = bool(entry) and not force

        if use_cache:
            tracks = self._dev.list_tracks(extended=False)
            merge_library_fields(tracks, entry.get("fields", {}))
            try:
                app._log(
                    f"[OK] 曲库快读 {len(tracks)} 首（命中本机序号 {serial[:12]}… 的缓存，"
                    f"缓存于 {entry.get('saved', '?')}）。")
            except Exception:
                pass
            return tracks

        tracks = self._dev.list_tracks(extended=True)
        if serial:
            fields = extended_fields_from_tracks(tracks)
            cache.setdefault("devices", {})[serial] = {
                "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(tracks),
                "fields": fields,
            }
            ok = save_library_cache(cache)
            try:
                app._log(
                    f"[OK] 已完整读取 {len(tracks)} 首并缓存该机器序号 "
                    f"{serial[:12]}…（{'已保存' if ok else '保存失败'}）；"
                    f"本次较慢，后续连接将自动快读。")
            except Exception:
                pass
        if force:
            try:
                app._force_full_library_read = False
            except Exception:
                pass
        return tracks


def config_dir():
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return HERE


THEME_CFG = config_dir() / "studio_theme.json"
DOCK_CFG = config_dir() / "studio_dock.json"
CONTROL_DOCK_CFG = config_dir() / "studio_control_dock.json"
LIB_CACHE_CFG = config_dir() / "njb3_library_cache.json"
LIB_CACHE_VERSION = 1

# 版本号：主版本.次版本.修订版本-编译日期(YYMMDD)
#   0 = 测试版；1 = 第一版；0 = 初版（后续修正功能递增第三位）；261123 = 2026-11-23 编译
APP_VERSION = "0.1.0-261123"
APP_BUILD_TAG = "156300by[AI]"


def serial_key(raw: bytes) -> str:
    """设备序列号主键：read_id() 原始字节转小写 hex。"""
    return bytes(raw).hex()


def load_library_cache():
    """读取曲库扩展字段缓存；任何异常都按空缓存处理，绝不阻断连接。"""
    try:
        with open(LIB_CACHE_CFG, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("version") != LIB_CACHE_VERSION:
            return {"version": LIB_CACHE_VERSION, "devices": {}}
        if not isinstance(d.get("devices"), dict):
            return {"version": LIB_CACHE_VERSION, "devices": {}}
        return d
    except Exception:
        return {"version": LIB_CACHE_VERSION, "devices": {}}


def save_library_cache(data):
    """原子写入曲库缓存，避免中途断电留下半截 JSON。"""
    tmp = str(LIB_CACHE_CFG) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, str(LIB_CACHE_CFG))
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def extended_fields_from_tracks(tracks):
    """从完整曲库抽取慢字段，按 trackid 建索引。"""
    fields = {}
    for t in tracks:
        tid = t.get("trackid")
        if tid is None:
            continue
        rec = {}
        for key in ("filename", "folder"):
            val = t.get(key)
            if val:
                rec[key] = val
        if rec:
            fields[str(int(tid))] = rec
    return fields


def merge_library_fields(basic_tracks, fields):
    """把缓存里的扩展字段按 trackid 合回实时 basic 曲库。

    基础字段（标题/艺人/时长等）始终以设备实时读取为准，缓存只补慢字段，
    因此设备增删曲目、改元数据都不会被陈旧缓存覆盖。
    """
    fields = fields or {}
    for t in basic_tracks:
        tid = t.get("trackid")
        if tid is None:
            continue
        rec = fields.get(str(int(tid)))
        if not rec:
            continue
        for key in ("filename", "folder"):
            if key in rec and not t.get(key):
                t[key] = rec[key]
    return basic_tracks


def load_theme_cfg():
    try:
        with open(THEME_CFG, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("name") in theme.theme_names():
            return d
    except Exception:
        pass
    return {"name": theme.FOLLOW_SYSTEM}


def save_theme_cfg(name):
    try:
        with open(THEME_CFG, "w", encoding="utf-8") as f:
            json.dump({"name": name}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- 默认布局
MAIN_IDS = ("machine", "playctrl", "other")
CONTROL_MODULE_IDS = ("devstat", "playback", "driver", "refresh", "appearance", "log")


def studio_default_tree(ids):
    """三个整页工作台：单页签节点隐藏页签栏，由顶部按钮切换 active。"""
    present = [pid for pid in MAIN_IDS if pid in set(ids)]
    if not present:
        return {"type": "tabs", "panels": [], "active": None}
    # v45：启动默认钉在【控制工作台】，设备连上后第一眼就是设备状态/播控。
    default_pid = "other" if "other" in present else present[0]
    return {"type": "tabs", "panels": present,
            "active": default_pid, "show_tabs": False}


def _ctl_leaf(pid):
    return {"type": "tabs", "panels": [pid], "active": pid, "show_tabs": False}


def _ctl_paned(orient, ids, ratios=None):
    kids = [_ctl_leaf(pid) for pid in ids]
    if not kids:
        return None
    if len(kids) == 1:
        return kids[0]
    return {"type": "paned", "orient": orient, "children": kids,
            "ratios": ratios or [1.0 / len(kids)] * len(kids)}


def control_default_tree(ids):
    """控制工作台默认分布（v37）：左列自上而下 设备状态 / 播放控制 /
    （设备信息与数据刷新 + 驱动安装）/ 界面外观；日志固定在右列。

    与用户当前摆放保持一致，且每个小框体都有独立 min_px，拖到下限即停。
    """
    def paned(orient, kids, ratios=None):
        kids = [k for k in kids if k]
        if not kids:
            return None
        if len(kids) == 1:
            return kids[0]
        return {"type": "paned", "orient": orient, "children": kids,
                "ratios": ratios or [1.0 / len(kids)] * len(kids)}

    present = set(ids)
    left_children = []
    for x in ("devstat", "playback"):
        if x in present:
            left_children.append(_ctl_leaf(x))
    # 下半区：刷新 + 驱动 上下叠，再与“界面外观”上下叠。
    lower = []
    rd = [_ctl_leaf(x) for x in ("refresh", "driver") if x in present]
    rd_node = paned("vertical", rd, [0.55, 0.45] if len(rd) == 2 else None)
    if rd_node:
        lower.append(rd_node)
    if "appearance" in present:
        lower.append(_ctl_leaf("appearance"))
    lower_node = paned("vertical", lower, [0.56, 0.44] if len(lower) == 2 else None)
    if lower_node:
        left_children.append(lower_node)
    left = paned("vertical", left_children,
                 [0.15, 0.17, 0.68] if len(left_children) == 3 else None)
    log_leaf = _ctl_leaf("log") if "log" in present else None
    if left and log_leaf:
        # v37：日志在右列，宽度上限 45%，左列模块最小 55%。
        return {"type": "paned", "orient": "horizontal",
                "children": [left, log_leaf], "ratios": [0.55, 0.45],
                "max_ratios": [1.0, 0.45], "min_ratio": 0.12}
    return left or log_leaf or {"type": "tabs", "panels": [], "active": None}


class _NBShim:
    """兼容原代码 self.nb.select(self.tab_xxx)：转发到整页切换。"""

    def __init__(self, app):
        self.app = app

    def select(self, widget):
        pid = getattr(self.app, "_tab_map", {}).get(widget)
        if pid:
            try:
                self.app.action_show_main(pid)
            except Exception:
                pass


class AppearanceDialog(tk.Toplevel):
    """外观设置（foobar2000 风格）：预设切换 + 实时预览 + 跟随系统 + WCAG 报告。"""

    def __init__(self, master, current, on_preview):
        super().__init__(master)
        self.title("外观设置")
        self.transient(master)
        self.resizable(False, False)
        self.on_preview = on_preview
        self.result = None
        self._current = current
        self._build()

    def _build(self):
        pal = theme.resolve(self._current)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="皮肤预设", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(f, text="选择即用，可实时预览；确定后保存。",
                  foreground=pal["text_dim"]).pack(anchor="w", pady=(2, 8))

        left = ttk.Frame(f)
        left.pack(side="left", fill="y")
        right = ttk.Frame(f, padding=(14, 0))
        right.pack(side="left", fill="both", expand=True)

        self.lst = tk.Listbox(left, height=9, width=16, exportselection=0,
                              bg=pal["input_bg"], fg=pal["text"],
                              selectbackground=pal["select_bg"], selectforeground=pal["select_fg"],
                              relief="solid", bd=1, font=("Microsoft YaHei UI", 10))
        for i, name in enumerate(theme.theme_names()):
            self.lst.insert("end", name)
            if name == self._current:
                self.lst.selection_set(i)
        self.lst.pack(side="left", fill="y")
        self.lst.bind("<<ListboxSelect>>", self._on_select)

        self.info = tk.Text(right, height=11, width=44, relief="solid", bd=1,
                            bg=pal["input_bg"], fg=pal["text"],
                            font=("Consolas", 9), wrap="word")
        self.info.pack(fill="both", expand=True)

        self._show_info(self._current)

        btns = ttk.Frame(f)
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right", padx=4)
        ttk.Button(btns, text="确定", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=4)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        self.grab_set()

    def _on_select(self, event):
        sel = self.lst.curselection()
        if not sel:
            return
        name = theme.theme_names()[sel[0]]
        self._current = name
        self._show_info(name)
        self.on_preview(name)

    def _show_info(self, name):
        pal = theme.resolve(name)
        audit = theme.audit(None if name == theme.FOLLOW_SYSTEM else name)
        chosen = theme.SYSTEM_LIGHT_THEME if (name == theme.FOLLOW_SYSTEM and theme.system_is_light()) \
            else (theme.SYSTEM_DARK_THEME if name == theme.FOLLOW_SYSTEM else name)
        a = audit.get(chosen, {})
        lines = [
            f"当前预览：{chosen}" + ("（跟随系统）" if name == theme.FOLLOW_SYSTEM else ""),
            "",
            "对比度校验（WCAG）：",
            f"  正文/面板      {a.get('text/panel', '-')}:1   （AA 达标 ≥4.5）",
            f"  次要文字/面板  {a.get('text_dim/panel', '-')}:1",
            f"  标题/标题栏    {a.get('header_fg/header', '-')}:1",
            f"  焦点文字/焦点底 {a.get('select_fg/select_bg', '-')}:1",
            "",
            "语义色：",
            f"  强调 {pal['accent']}   焦点 {pal['select_bg']}",
            f"  失焦 {pal['idle_bg']}   舞台 {pal['warm_bg']}",
            f"  背景 {pal['bg']}   面板 {pal['panel']}",
            "",
            "工作台提示：",
            "  顶栏三按钮切换整页；控制工作台内小框体可拖动、贴边、调大小。",
            "  日志上拖有上限；播控侧栏折叠后留竖线与箭头按钮。",
        ]
        self.info.delete("1.0", "end")
        self.info.insert("1.0", "\n".join(lines))

    def _ok(self):
        self.result = self._current
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class StudioApp(base.App):
    """新版 UI：仅重建界面，业务逻辑全部继承。"""

    MAIN_VIEW_BUTTONS = (
        ("machine", "传输工作台"),
        ("playctrl", "播控工作台"),
        ("other", "控制工作台"),
    )

    def __init__(self, root):
        cfg = load_theme_cfg()
        self.theme_name = cfg.get("name", theme.FOLLOW_SYSTEM)
        self.pal = theme.resolve(self.theme_name)
        # v35：音量/EAX 需要在两个工作台之间共享同一个变量对象，先于 UI 构建创建。
        self.vol_var = tk.IntVar(root, value=50)
        self.eax_on_var = tk.BooleanVar(root, value=False)
        super().__init__(root)
        # v36：传输进度弹窗右侧状态、播控映射等辅助状态。
        self.progress_stat_var = tk.StringVar(root, value="")
        # v41：删除曲目专用进度（仅“删除选中”弹窗显示 xx/xx）。
        self._delete_progress_active = False
        self._delete_progress_total = 0
        self.progress_pb = None
        # v41：每个播放清单只向设备读一次成员；两侧树选择/重绘都不再轮询。
        self._pl_checked_members = set()
        self._pl_restore_guard = False
        self._device_order = []
        self._play_stage_index = None
        self._transfer_finished_job = None
        # v46：手动“刷新完整曲库”时置位，代理消费后清掉，强制走 extended 完整读。
        self._force_full_library_read = False
        self._watcher = theme.SystemThemeWatcher(
            self.root, self._on_system_theme_change)
        if self.theme_name == theme.FOLLOW_SYSTEM:
            self._watcher.start()

    # -------------------------------------------------------------- 主题
    def _on_system_theme_change(self, is_light):
        if getattr(self, "theme_name", None) == theme.FOLLOW_SYSTEM:
            self.apply_studio_theme(theme.FOLLOW_SYSTEM, save=False)

    def apply_studio_theme(self, name, save=True):
        self.theme_name = name
        self.pal = theme.apply_theme(self.root, name)
        p = self.pal
        self.skin = {
            "theme": "clam",
            "bg": p["bg"], "fg": p["text"],
            "select_bg": p["select_bg"], "select_fg": p["select_fg"],
            "idle_bg": p["idle_bg"], "idle_fg": p["idle_fg"],
            "stage_bg": p["warm_bg"], "stage_fg": p["warm_fg"],
            "accent": p["accent"], "ok": p["ok"], "warn": p["warn"],
            "font_family": "Microsoft YaHei UI", "font_size": 9,
        }
        self.skin_name = name
        try:
            self._configure_tree_style_map()
            self._apply_focus_highlight()
        except Exception:
            pass
        d = getattr(self, "dock", None)
        if d:
            d.set_palette(p)
        cd = getattr(self, "control_dock", None)
        if cd:
            cd.set_palette(p)
        for f in getattr(self, "_chrome_frames", []):
            try:
                f.configure(bg=p["chrome"])
            except Exception:
                pass
        for lbl, role in getattr(self, "_chrome_labels", []):
            try:
                lbl.configure(bg=p["chrome"], fg=p.get(role, p["text"]))
            except Exception:
                pass
        sl = getattr(self, "status_lbl", None)
        if sl is not None:
            try:
                sl.configure(bg=p["chrome"])
            except Exception:
                pass
        ib = getattr(self, "info_bar", None)
        if ib is not None:
            try:
                ib.configure(bg=p["chrome"])
            except Exception:
                pass
        for lbl in getattr(self, "info_labels", {}).values():
            try:
                lbl.configure(foreground=p["text_dim"])
            except Exception:
                pass
        for handles in (getattr(self, "_pc_handles", {}),
                        getattr(self, "_wb_handles", {})):
            for side, widgets in handles.items():
                line, btn = widgets
                try:
                    line.configure(bg=p["border"])
                    btn.configure(bg=p["header"], fg=p["header_fg"],
                                  activebackground=p["accent"])
                except Exception:
                    pass
        # v39：所有可拖动分隔条跟随主题色（用 sash token，缺失回退 border）。
        for sash in getattr(self, "_theme_sashes", []):
            try:
                if sash.winfo_exists():
                    sash.configure(bg=p.get("sash", p["border"]))
            except Exception:
                pass
        # v41：传输队列的 tk.PanedWindow 分隔条与元数据输入画布原先在深色主题下
        # 创建，切换浅色主题后没有跟着换色，留下两条“黑条”。这里一并刷新并收窄。
        tp = getattr(self, "transfer_paned", None)
        if tp is not None:
            try:
                tp.configure(bg=p.get("sash", p["border"]), sashwidth=4)
            except Exception:
                pass
        mv = getattr(self, "_meta_view", None)
        if mv is not None:
            try:
                mv.configure(bg=p["panel"])
            except Exception:
                pass
        cb = getattr(self, "skin_combo", None)
        var = getattr(self, "skin_name_var", None)
        if cb is not None:
            cb.configure(values=theme.theme_names())
        if var is not None:
            var.set(name)
        if save:
            save_theme_cfg(name)
        w = getattr(self, "_watcher", None)
        if w:
            w.stop()
            if name == theme.FOLLOW_SYSTEM:
                w.start()

    def _apply_current_skin(self, skin=None, name=None):
        self.apply_studio_theme(name or getattr(self, "theme_name", theme.FOLLOW_SYSTEM),
                                save=False)

    def on_skin_preset_changed(self, event=None):
        name = self.skin_name_var.get()
        self.apply_studio_theme(name)
        self._log(f"[OK] 已切换皮肤：{name}")

    def action_skin_settings(self):
        before = self.theme_name
        dlg = AppearanceDialog(self.root, before,
                               on_preview=lambda nm: self.apply_studio_theme(nm, save=False))
        self.root.wait_window(dlg)
        if dlg.result is None:
            self.apply_studio_theme(before, save=False)
        else:
            self.apply_studio_theme(dlg.result)
            self._log(f"[OK] 外观设置已保存：{dlg.result}")

    def action_skin_import(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="导入主题", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                name = json.load(f).get("name")
            if name not in theme.theme_names():
                raise ValueError("未知主题名")
            self.apply_studio_theme(name)
            self._log(f"[OK] 已导入主题：{name}")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    def action_skin_export(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title="导出主题", defaultextension=".json",
            initialfile=f"NJB3主题_{self.theme_name}.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"name": self.theme_name}, f, ensure_ascii=False, indent=2)
            self._log(f"[OK] 主题已导出：{path}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    # -------------------------------------------------------------- 整页切换
    def studio_default_layout(self, ids):
        return studio_default_tree(ids)

    def action_show_main(self, pid):
        tabs = self.dock._find_tabs_of(pid)
        if tabs and len(tabs.get("panels", [])) > 1:
            tabs["active"] = pid
            self.dock.save()
            self.dock.relayout()
        else:
            self.dock.focus_panel(pid)
        # 面板从隐藏变为可见后需重设内部 sash，否则第一栏会被算成 0 宽。
        if pid == "playctrl":
            self._pc_push_sash()
        elif pid == "machine":
            self._push_workbench_sash()
        self._sync_main_view_buttons()

    def _sync_main_view_buttons(self):
        buttons = getattr(self, "main_view_buttons", {})
        if not buttons:
            return
        tabs = None
        for pid in MAIN_IDS:
            n = self.dock._find_tabs_of(pid)
            if n:
                tabs = n
                break
        active = tabs.get("active") if tabs else None
        if active not in MAIN_IDS:
            active = next((pid for pid in MAIN_IDS
                           if self.dock.panels.get(pid)
                           and self.dock.panels[pid].outer.winfo_ismapped()), None)
        for pid, btn in buttons.items():
            label = dict(self.MAIN_VIEW_BUTTONS).get(pid, pid)
            mark = "● " if pid == active else "   "
            try:
                btn.configure(text=mark + label)
            except Exception:
                pass

    # ------------------------------------------------------ v45：播控模式总开关
    def action_pcmode_enable(self):
        """开启播控模式：沿用基类流程，仅在确认弹窗中加入单次播放建议。"""
        if not getattr(self, "connected", False):
            messagebox.showinfo("提示", "请先连接设备，再开启播控模式。")
            return
        if not messagebox.askyesno(
                "开启播控模式",
                "此操作将清空播放器内现有的“选定的音乐”（播放队列），\n"
                "之后完全由本软件的播控清单控制播放。\n\n"
                "强烈建议NJB3使用单次播放模式：播放机播完当前音乐会停止，\n"
                "省去连播与软件播控相互冲突的麻烦。\n\n"
                "确认清空机器队列并开启软件控制的播控模式？"):
            return

        def fn(d):
            ok = d.clear_play_queue()
            self.q.put(("log", f"[{'OK' if ok else '!'}] 已清空机器“选定的音乐”队列。"))
            self.q.put(("pcmode_enabled", ok))

        self._worker(fn, label="正在清空机器队列并开启播控模式 ...")

    # -------------------------------------------------------------- v36：进度弹窗与自定义事件
    def _show_progress(self, text: str):
        self.progress_stat_var.set("")
        super()._show_progress(text)

    def _hide_progress(self):
        """弹窗关闭即退出删除计数模式，后续刷新/上传弹窗不受影响。"""
        self._delete_progress_active = False
        self._delete_progress_total = 0
        self.progress_pb = None
        super()._hide_progress()

    def _worker(self, fn, *args, label=None, quiet=False):
        """v42：后台任务统一经安全设备代理，空清单超时不再拖垮连接。"""
        if getattr(fn, "_no_pl_proxy", False):
            return super()._worker(fn, *args, label=label, quiet=quiet)

        def wrapped(d, *a, **kw):
            return fn(_SafePlaylistDevice(d, self), *a, **kw)
        for attr in ("_safe_retry", "_no_popup", "_no_pl_proxy"):
            if hasattr(fn, attr):
                setattr(wrapped, attr, getattr(fn, attr))
        return super()._worker(wrapped, *args, label=label, quiet=quiet)

    def action_delete_tracks(self):
        """v42：删除前先把受影响播放清单重建，避免固件把清单删坏。

        libnjb protocol3.c:2483 明确记录：当一个播放清单里的曲目被全部删掉，
        设备会返回 corrupt empty playlist（00 0e 00 00 00 01），随后读该清单
        会超时并触发固件 rebuilding library。做法是先用 replace_playlist 做
        copy-on-write：新建同名清单（可为空）→ 删除旧清单，再删曲目。
        """
        sel = self._sel_lib()
        if not sel:
            messagebox.showinfo("提示", "请先在机器歌曲里选中曲目。")
            return
        names = "\n".join(f'  - {t.get("title","?")}' for t in sel[:12])
        if not messagebox.askyesno(
                "确认删除", f"确定从设备删除这 {len(sel)} 首？\n{names}"):
            return
        ids = [int(t["trackid"]) for t in sel if t.get("trackid") is not None]
        deleted = set(ids)
        total = len(ids)
        if not total:
            return
        self._delete_progress_active = True
        self._delete_progress_total = total
        self.progress_stat_var.set(f"0/{total}")
        self.q.put(("progress_stat", f"0/{total}"))

        def fn(d):
            # 1) 先把所有含被删曲目的清单重建一遍（空清单也重建，不能靠删歌清空）。
            valid = {t.get("trackid") for t in getattr(self, "tracks", [])} - deleted
            cache = getattr(self, "pl_members", {})
            retargets = []
            for pl in list(getattr(self, "playlists", []) or []):
                pid = pl.get("playlistid")
                if pid is None:
                    continue
                members = list(cache.get(pid, []))
                if pid not in cache:
                    try:
                        members = list(d.get_playlist_tracks(pid))
                    except Exception:
                        members = []
                if not any(m in deleted for m in members):
                    continue
                name = pl.get("name") or f"清单{pid}"
                remain = [m for m in members if m not in deleted and m in valid]
                newid = d.replace_playlist(int(pid), name, remain)
                self.q.put(("pl_members_replace", pid, newid, remain))
                if pid == getattr(self, "cur_playlist", None):
                    retargets.append((pid, newid))
                self._log(f"[OK] 清单【{name}】已剔除 {len(members) - len(remain)} 首已删曲目"
                          f"（ID {pid} → {newid}）。")
            try:
                self.q.put(("playlists", d.list_playlists()))
            except Exception:
                pass
            # 先让新 ID 进入清单树，再重定选中项，否则选中会落空。
            for oldid, newid in retargets:
                self.q.put(("playlist_retarget", oldid, newid))

            # 2) 再删曲目本体；任何清单重建失败都会先抛错，不会走到这里。
            done = 0
            for tid in ids:
                d.delete_item(tid)
                done += 1
                self.q.put(("progress_stat", f"{done}/{total}"))
                self._log(f"[OK] 已删除 ID {tid}")

            # 3) 同步本地曲库/播放队列，陈旧 ID 不再参与后续渲染。
            left = [t for t in getattr(self, "tracks", [])
                    if t.get("trackid") not in deleted]
            self.q.put(("lib", left))
            stage_ids = [t.get("trackid") for t in getattr(self, "stage_tracks", [])
                         if t.get("trackid") not in deleted]
            if stage_ids is not None and getattr(self, "stage_tracks", None):
                self.q.put(("stage_tracks", None, stage_ids))

        self._worker(fn, label=f"正在删除选中的 {total} 首歌曲 ...")

    def _create_progress_win(self):
        """在首行“正在与播放器通讯”右侧显示“进度 xx%   n/N”。

        v39：
          · 只有【传输文件】任务才显示“传完本首后停止”按钮；刷新曲库/清单等
            只读任务不显示该按钮（二者共用同一个弹窗，靠 _transfer_active 区分）。
          · 弹窗整体跟随当前外观配色，不再固定白底黑字。
        """
        self.progress_after = None
        if self.progress_win is not None or not self.root.winfo_exists():
            return
        p = self.pal
        win = tk.Toplevel(self.root)
        win.title("正在通讯")
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.configure(bg=p["panel"])
        except Exception:
            pass
        head = tk.Frame(win, bg=p["panel"])
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, textvariable=self.progress_title_var,
                 font=("Microsoft YaHei UI", 11, "bold"),
                 bg=p["panel"], fg=p["text"]).pack(side="left")
        tk.Label(head, textvariable=self.progress_stat_var,
                 font=("Microsoft YaHei UI", 9, "bold"),
                 bg=p["panel"], fg=p.get("ok", "#4fc98a")).pack(side="right")
        # v41：删除选中用确定进度条 + n/N；其他任务仍是原来的不确定滚动条。
        deleting = bool(getattr(self, "_delete_progress_active", False))
        if deleting:
            pb = ttk.Progressbar(win, mode="determinate", length=330,
                                 maximum=max(1, self._delete_progress_total))
        else:
            pb = ttk.Progressbar(win, mode="indeterminate", length=330)
            pb.start(12)
        self.progress_pb = pb
        pb.pack(fill="x", padx=18, pady=4)
        foot = tk.Frame(win, bg=p["panel"])
        foot.pack(fill="x", padx=18, pady=(4, 16))
        tk.Label(foot, textvariable=self.progress_detail_var,
                 bg=p["panel"], fg=p.get("text_dim", "#9aa6b2"),
                 wraplength=220, justify="left").pack(side="left", fill="x", expand=True)
        # v39：仅传输任务显示“传完本首后停止”。
        self.progress_stop_btn = None
        if getattr(self, "_transfer_active", False):
            self.progress_stop_btn = ttk.Button(
                foot, text="传完本首后停止", command=self.action_stop, width=14)
            self.progress_stop_btn.pack(side="right", padx=(10, 0))
            try:
                self.progress_stop_btn.configure(
                    state=str(self.stop_btn.cget("state")))
            except Exception:
                pass
        win.update_idletasks()
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        try:
            win.grab_set()
        except Exception:
            pass
        self.progress_win = win

    def _handle(self, ev):
        kind = ev[0] if ev else None
        if kind == "progress_stat":
            txt = str(ev[1])
            self.progress_stat_var.set(txt)
            pb = getattr(self, "progress_pb", None)
            if pb is not None and str(pb.cget("mode")) == "determinate":
                try:
                    tail = txt.strip().split()[-1]
                    done = int(tail.split("/")[0])
                    pb["value"] = done
                except Exception:
                    pass
            return
        if kind == "playlist_retarget":
            oldid, newid = ev[1], ev[2]
            # v42：只有当前选中的清单被重建时才改选中项；批量删除会重建多个
            # 清单，不能让最后一个新 ID 抢走用户原本的选择。
            if newid and newid != oldid and getattr(self, "cur_playlist", None) == oldid:
                self.cur_playlist = newid
                # v41：copy-on-write 产生的新 ID 已有本地全量成员，不必再读设备。
                getattr(self, "_pl_checked_members", set()).add(newid)
                for tree in (getattr(self, "pl_tree", None),
                             getattr(self, "pl_tree2", None)):
                    if tree is not None and tree.winfo_exists():
                        iid = f"pl::{newid}"
                        if tree.exists(iid):
                            tree.selection_set(iid)
                            tree.focus(iid)
                self._sync_playlist_song_titles()
            return
        if kind == "pl_members_replace":
            # v37：0x0107 copy-on-write 后清单内容=本次提交的全量成员，
            # 用全量覆盖本地缓存（而非 extend），避免新旧重复显示。
            oldid, newid, members = ev[1], ev[2], ev[3]
            try:
                checked = getattr(self, "_pl_checked_members", None)
                if checked is not None:
                    checked.add(newid)
                    checked.discard(oldid)
                if oldid != newid:
                    self.pl_members.pop(oldid, None)
                self.pl_members[newid] = list(members)
                if getattr(self, "cur_playlist", None) == oldid:
                    byid = {t.get("trackid"): t for t in getattr(self, "tracks", [])}
                    self.pl_tracks = [byid[m] for m in members if m in byid]
                    self._render_pl_songs()
            except Exception:
                pass
            return
        if kind == "transfer_finished":
            if self._transfer_finished_job is not None:
                try:
                    self.root.after_cancel(self._transfer_finished_job)
                except Exception:
                    pass
            self._transfer_finished_job = self.root.after(
                150, lambda: self._post_transfer_refresh(bool(ev[1])))
            return
        if kind == "play_sync":
            ev = self._calibrate_play_sync(ev)
            super()._handle(ev)
            self._remap_play_sync(ev)
            return
        if kind == "pcmode_enabled":
            super()._handle(ev)
            # v39：播控模式开启/关闭后同步“软件播放”按钮可用性。
            self._sync_conn_buttons()
            return
        if kind == "eax_read":
            super()._handle(ev)
            # v40：基类按机器是否开放 EAX 禁用它的复选框；播控页那枚副本
            # 只跟随连接状态，避免设备信息刷新时被周期性禁用/启用。
            w = getattr(self, "eax2_chk", None)
            if w is not None:
                try:
                    w.configure(state="normal" if getattr(self, "connected", False)
                                else "disabled")
                except Exception:
                    pass
            return
        if kind == "owner_name_read":
            # 延迟到 worker 的 busy=False 事件处理完再弹输入框，否则写入任务被拒。
            self._owner_name_cache = ev[1] if len(ev) > 1 else ""
            self.root.after(120, lambda: self._prompt_owner_name(self._owner_name_cache))
            return
        super()._handle(ev)

    def _prompt_owner_name(self, current):
        """在读到机主名后弹输入框（供 owner_name_read 事件延迟调用）。"""
        value = tk.simpledialog.askstring(
            "机主名", "输入机主名：", initialvalue=current or "", parent=self.root)
        if not value:
            return
        self._owner_name_cache = value

        def write_fn(d, v=value):
            self._write_owner_frame(d, v)
            self.q.put(("log", f"[OK] 机主名已设置为 {v}"))
        write_fn._safe_retry = True   # 写名字会触发设备改名重连，允许安全重试
        self._worker(write_fn, label="正在设置机主名 ...")

    def _sync_stop_buttons(self, state):
        """主界面隐藏按钮与进度弹窗按钮的停止状态保持同步。"""
        for name in ("stop_btn", "progress_stop_btn"):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    if w.winfo_exists():
                        w.configure(state=state)
                except Exception:
                    pass

    def action_sort_lib(self, col):
        """v39：曲库表头排序后加升降序箭头（两个曲库表同步）。"""
        super().action_sort_lib(col)
        asc = self.lib_sort[1] if getattr(self, "lib_sort", None) else True
        for name in ("lib_tree", "lib_tree2"):
            tree = getattr(self, name, None)
            if tree is not None and col in tree["columns"]:
                self._mark_sort_arrow(tree, col, asc)

    def on_lib_double(self, event):
        """v39：时长列不允许双击修改（时长来自文件/设备，不是可编辑元数据）。"""
        try:
            widget = event.widget
            if widget.identify_region(event.x, event.y) == "cell":
                col = widget.identify_column(event.x)
                idx = int(col.replace("#", "")) - 1
                cols = getattr(self, "LIB_COLS", ())
                if 0 <= idx < len(cols) and cols[idx] == "length":
                    return "break"
        except Exception:
            pass
        return super().on_lib_double(event)

    def _update_play_track_label(self):
        """v39：队列号一律 1-based——第 1 首显示 [1/N]，不再出现 [0/N]。"""
        pos = self.play_queue_pos
        total = len(getattr(self, "stage_tracks", []))
        info = getattr(self, "play_track_info", None)
        cur = pos if isinstance(pos, int) and pos >= 0 else None
        if isinstance(cur, int) and 0 <= cur < total and getattr(self, "stage_tracks", None):
            info = self.stage_tracks[cur]
        if info:
            title = str(info.get("title") or "未命名")
            artist = str(info.get("artist") or "")
            text = "当前曲目：" + title + (f" — {artist}" if artist else "")
        else:
            text = "当前曲目：未知（播放后自动同步）"
        if cur is not None:
            text += f"   [{cur + 1}/{total}]" if total else f"   [{cur + 1}/?]"
        self.play_track_var.set(text)

    def _post_transfer_refresh(self, complete=True):
        """传完（含“传完本首后停止”）后：刷新曲库+播放清单；全部完成才清空队列。"""
        self._transfer_finished_job = None
        self._transfer_active = False
        self._sync_stop_buttons("disabled")
        # v39：无论全部完成还是中途“传完本首后停止”，都要同步曲库和播放清单。
        try:
            if getattr(self, "connected", False):
                self.action_refresh_lib_playlists()
        except Exception:
            pass
        if complete:
            self.queue_rows.clear()
            try:
                self._render_queue()
            except Exception:
                pass
            self._log("[OK] 传输完成：曲库和播放清单已刷新，传输队列已清空。")
        else:
            self._log("[OK] 已停止后续传输：曲库和播放清单已同步（未完成条目仍留在队列）。")

    # v39：清单内歌曲列（新增 专辑/流派，且可点表头排序）
    PL_SONG_COLS = ("num", "title", "artist", "album", "genre", "id")
    PL_SONG_HEADS = ("#", "标题", "艺人", "专辑", "流派", "ID")
    PL_SONG_WIDTHS = (34, 170, 110, 120, 90, 60)

    def _upgrade_pl_songs_columns(self, tree):
        """给“清单内的歌曲”树加 专辑/流派 两列，并让表头可点击排序。"""
        if tree is None:
            return
        try:
            tree.configure(columns=self.PL_SONG_COLS)
            for c, h, w in zip(self.PL_SONG_COLS, self.PL_SONG_HEADS, self.PL_SONG_WIDTHS):
                tree.heading(c, text=h,
                             command=lambda cc=c, t=tree: self.action_sort_pl_songs(t, cc))
                tree.column(c, width=w, anchor="w")
        except Exception:
            pass

    def action_sort_pl_songs(self, tree, col):
        """清单内歌曲表头排序：同列切换升/降，并更新表头箭头。"""
        if col == "num":
            return
        key = id(tree)
        state = getattr(self, "_pl_song_sort", None)
        if state is None:
            state = self._pl_song_sort = {}
        cur = state.get(key, ("num", True))
        asc = (not cur[1]) if cur[0] == col else True
        state[key] = (col, asc)
        records = list(getattr(self, "pl_tracks", []) or [])
        try:
            records.sort(key=lambda t: self._sort_key_text(t.get(col, "")), reverse=not asc)
        except Exception:
            pass
        self._render_pl_songs(sorted_tracks=records, only_tree=tree)
        self._mark_sort_arrow(tree, col, asc)

    def _mark_sort_arrow(self, tree, col, asc):
        """表头最右侧加排序箭头：下箭头=升序，上箭头=倒序；同时清除其它列旧箭头。"""
        import re as _re
        try:
            heads = {}
            for c in tree["columns"]:
                txt = str(tree.heading(c, "text") or "")
                clean = _re.sub(r"\s+[↓↑]\s*$", "", txt)
                heads[c] = clean
                tree.heading(c, text=clean)
            base = heads.get(col, col)
            tree.heading(col, text=f"{base}  {'↓' if asc else '↑'}")
        except Exception:
            pass

    def _current_pl_name(self):
        """当前播放清单的名字（从 pl_tree/pl_tree2 的选中项取）。"""
        try:
            for name in ("pl_tree2", "pl_tree"):
                tree = getattr(self, name, None)
                if tree is None:
                    continue
                sel = tree.selection()
                if sel:
                    txt = str(tree.item(sel[0], "text") or "")
                    # 形如 “舒缓音乐 [387 首]”
                    return txt.split(" [")[0].strip()
        except Exception:
            pass
        return "清单"

    def _pl_songs_with_selection(self):
        """v40：两套“清单内歌曲”都可用；优先取当前有选中的那一套。"""
        trees = [getattr(self, "pl_songs2", None), getattr(self, "pl_songs", None)]
        focus = getattr(self, "_last_focus_tree", None)
        if focus in trees:
            trees = [focus] + [t for t in trees if t is not focus]
        for tree in trees:
            if tree is not None and tree.winfo_exists() and tree.selection():
                return tree
        return trees[0] if trees else None

    def action_pl_remove_selected(self):
        """从当前播放清单移除选中曲目（copy-on-write 重建，保留其余）。"""
        tree = self._pl_songs_with_selection()
        sel = list(tree.selection()) if tree is not None else []
        if not sel:
            messagebox.showinfo("提示", "请先在‘清单内的歌曲’里选中曲目。", parent=self.root)
            return
        remove_pos = set()
        for iid in sel:
            if str(iid).startswith("ps::"):
                try:
                    remove_pos.add(int(str(iid)[4:]))
                except Exception:
                    pass
        if not remove_pos:
            return
        tracks = list(getattr(self, "pl_tracks", []) or [])
        remain = [int(t.get("trackid")) for i, t in enumerate(tracks) if i not in remove_pos]
        self._rewrite_current_playlist(remain, f"已移除 {len(remove_pos)} 首")

    def action_pl_clear(self):
        """清空当前播放清单的全部曲目。"""
        if not getattr(self, "pl_tracks", None):
            messagebox.showinfo("提示", "当前清单已经是空的。", parent=self.root)
            return
        if not messagebox.askyesno("清空清单", "确定清空当前播放清单的全部曲目？\n（只清清单，不删除机器里的歌曲）",
                                   parent=self.root):
            return
        self._rewrite_current_playlist([], "已清空清单")

    def _rewrite_current_playlist(self, trackids, note):
        """用 replace_playlist 按新成员重建当前清单，并同步本地缓存。"""
        plid = getattr(self, "cur_playlist", None)
        if plid is None:
            messagebox.showinfo("提示", "请先在‘播放清单’里选中一个清单。", parent=self.root)
            return
        name = self._current_pl_name()
        old_members = list(getattr(self, "pl_members", {}).get(plid, []))

        def fn(d):
            newid = d.replace_playlist(int(plid), name, [int(x) for x in trackids])
            self.q.put(("pl_members_replace", plid, newid, list(trackids)))
            self.q.put(("playlist_retarget", plid, newid))
            self.q.put(("playlists", d.list_playlists()))
            self._log(f"[OK] {note}：清单【{name}】ID {plid} → {newid}，现 {len(trackids)} 首。")
        self._worker(fn, label=f"正在{note} ...")

    def _add_pl_song_buttons(self, pit, tree=None):
        """在指定‘清单内的歌曲’框体底部加 移除已选 / 清空清单 按钮。

        v40：这两个按钮只属于【传输工作台】的 pl_songs2；播控工作台不再添加。
        """
        if pit is None:
            return
        # 防止重复添加
        for w in pit.winfo_children():
            if isinstance(w, ttk.Frame) and getattr(w, "_pl_song_btnbar", False):
                return
        bar = ttk.Frame(pit)
        bar._pl_song_btnbar = True
        ttk.Button(bar, text="移除已选", command=self.action_pl_remove_selected,
                   width=9).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="清空清单", command=self.action_pl_clear,
                   width=9).pack(side="left")
        # 先让按钮条占据底部一行，再让树/滚动条重排填充剩余空间；
        # 否则树 expand=True 先占位会把按钮条挤成 0 高。
        if tree is None:
            tree = getattr(self, "pl_songs", None)
        sb = None
        for w in pit.winfo_children():
            if isinstance(w, ttk.Scrollbar):
                sb = w
                break
        bar.pack(side="bottom", fill="x", pady=(3, 2))
        if tree is not None:
            tree.pack_forget()
            tree.pack(side="left", fill="both", expand=True)
        if sb is not None:
            sb.pack_forget()
            sb.pack(side="right", fill="y")

    def _decode_owner_name(self, raw):
        """按 libnjb njb3_read_string_frame 解析 0x0113 机主名。

        响应布局：status(2) + strlen(2) + frameid(2) + UCS2-BE 字符串 + NUL。
        """
        try:
            b = bytes(raw)
            if len(b) < 8:
                return ""
            strlen = int.from_bytes(b[2:4], "big")
            payload = b[6:6 + strlen] if 0 < strlen <= len(b) - 6 else b[6:]
            candidates = [payload]
            if len(payload) >= 4:
                candidates.append(payload[4:])   # 兼容带帧头/长度前缀的响应
            for cand in candidates:
                s = cand.decode("utf-16-be", errors="ignore").split("\x00")[0].strip()
                if s and all(ch.isprintable() for ch in s):
                    return s
        except Exception:
            pass
        return ""

    @staticmethod
    def _write_owner_frame(d, name):
        """按 libnjb njb3_set_owner_string 写 0x0113（scripts 里的旧实现少了尾部 2 字节）。

        libnjb: cmd = 00 07 00 01 <len=unilen+4:2> 01 13 <UCS2+NUL> 00 00
        """
        u = base.pde.str_to_ucs2(name)
        length = len(u) + 2
        cmd = bytes.fromhex("00 07 00 01") + base.pde.be16(length) \
            + bytes.fromhex("01 13") + u + b"\x00\x00"
        d.send_only(cmd)
        if d._status_only() != base.pde.STATUS_OK:
            raise base.pde.PDEError("set_owner failed")

    def action_owner(self):
        """v39：设置机主名时先读现有名字预填输入框（0x0113 可读写）。"""
        if not getattr(self, "connected", False):
            messagebox.showinfo("提示", "请先连接设备，再设置机主名。")
            return
        current = getattr(self, "_owner_name_cache", "")

        def read_fn(d):
            name = ""
            try:
                raw = d.read_register(base.pde.F_OWNER)
                name = self._decode_owner_name(raw)
            except Exception as e:
                self._log(f"[!] 读取机主名失败：{e}")
            self.q.put(("owner_name_read", name))
            return name
        read_fn._safe_retry = True
        self._worker(read_fn, label="正在读取机主名 ...")

    @staticmethod
    def _estimate_mp3_length(path):
        """v39：纯 Python 估算 MP3 时长（秒），无第三方依赖。

        njb3_transfer.read_id3 的 Tags 没有 length 字段，导致上传时 0x0105
        时长帧从不写入，设备显示默认 18:12:15(0xFFFF)。这里从 ID3v2 之后
        的第一个 MPEG 帧头取比特率/采样率，按 CBR 估算；能解析 Xing 帧时
        用帧数得到精确时长。失败返回 0（调用方跳过写入）。
        """
        try:
            BITRATES = {
                # (mpeg_ver, layer): [索引1..14 的 kbps]
                (3, 3): [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],  # MPEG1 L3
                (2, 3): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],      # MPEG2 L3
                (0, 3): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],      # MPEG2.5 L3
                (3, 2): [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],  # MPEG1 L2
                (2, 2): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
                (0, 2): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
                (3, 1): [32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],  # L1
                (2, 1): [32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
                (0, 1): [32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
            }
            SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
            SPF = {3: {3: 1152, 2: 1152, 1: 384},
                   2: {3: 576, 2: 1152, 1: 384},
                   0: {3: 576, 2: 1152, 1: 384}}
            with open(path, "rb") as f:
                data = f.read(2 * 1024 * 1024)  # 前 2MB 足够找首帧/Xing
            if len(data) < 4:
                return 0
            off = 0
            # 跳过 ID3v2
            if data[:3] == b"ID3":
                size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
                       ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
                off = 10 + size
            # 找 MPEG 帧同步字
            while off + 4 <= len(data):
                if data[off] == 0xFF and (data[off + 1] & 0xE0) == 0xE0:
                    break
                off += 1
            if off + 4 > len(data):
                return 0
            h = data[off:off + 4]
            ver_bits = (h[1] >> 3) & 0x03
            mpeg_ver = {3: 3, 2: 2, 0: 0}.get(ver_bits)
            layer_bits = (h[1] >> 1) & 0x03
            layer = 4 - layer_bits  # 3=L3,2=L2,1=L1
            br_idx = (h[2] >> 4) & 0x0F
            sr_idx = (h[2] >> 2) & 0x03
            pad = (h[2] >> 1) & 0x01
            if mpeg_ver is None or layer not in (1, 2, 3) or br_idx in (0, 15) or sr_idx == 3:
                return 0
            brs = BITRATES.get((mpeg_ver, layer))
            srs = SR.get(mpeg_ver)
            if not brs or not srs or br_idx > len(brs):
                return 0
            bitrate = brs[br_idx - 1] * 1000
            samplerate = srs[sr_idx]
            spf = SPF[mpeg_ver][layer]
            frame_len = int((spf / 8.0) * bitrate / samplerate) + pad
            if frame_len <= 0:
                return 0
            # Xing/Info 帧：取帧数
            xoff = off + (32 if mpeg_ver == 3 else 17)
            if layer == 3:
                xoff = off + (36 if mpeg_ver == 3 else 21)
            # 单声道 Xing 偏移减半（粗略：从公共位置附近搜索）
            for start in range(xoff - 4, xoff + 8, 4):
                if start + 12 <= len(data) and data[start:start + 4] in (b"Xing", b"Info"):
                    flags = int.from_bytes(data[start + 4:start + 8], "big")
                    if flags & 1:
                        frames = int.from_bytes(data[start + 8:start + 12], "big")
                        if 0 < frames < 10_000_000:
                            sec = frames * spf / float(samplerate)
                            if 0 < sec < 86400:
                                return int(round(sec))
                    break
            # CBR 估算：音频数据长度 ≈ 文件大小 - ID3v2 头 - ID3v1 尾
            import os as _os
            total = _os.path.getsize(path)
            audio_bytes = max(0, total - off - (128 if total >= 128 else 0))
            sec = audio_bytes * 8.0 / bitrate
            if 0 < sec < 86400:
                return int(round(sec))
            return 0
        except Exception:
            return 0

    def _ensure_track_length(self, row):
        """上传前补齐 tags['length']（秒），使设备写入 0x0105 时长帧。"""
        tags = row.get("tags") or {}
        try:
            length = int(tags.get("length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            length = self._estimate_mp3_length(row.get("path", ""))
            if length > 0:
                tags["length"] = length
                self._log(f"[+] 已估算【{tags.get('filename', row.get('path'))}】"
                          f"时长 {length}s，将写入播放器。")
        return length

    def _append_playlist_keep_existing(self, d, plid, new_trackids):
        """v37：0x0107 在本机是 copy-on-write——设备返回新 ID，新清单只含本次
        提交的曲目，原先的成员会丢失。写入前先读回该清单现有成员，与本次新曲
        合并（去重、保持顺序）后一次性提交，从而保留原有歌曲。

        返回 (newid, 合并后的全量 trackids)。没有任何清除清单的指令。
        """
        plid = int(plid)
        try:
            existing = [int(x) for x in (d.get_playlist_tracks(plid) or [])]
        except Exception as e:
            self._log(f"[!] 读取清单 ID {plid} 现有成员失败：{e}")
            existing = []
        merged, seen = [], set()
        for tid in list(existing) + [int(x) for x in new_trackids]:
            if tid not in seen:
                seen.add(tid)
                merged.append(tid)
        newid = d.add_tracks_to_playlist(plid, merged)
        return newid, merged

    def _sync_playlist_song_titles(self):
        """“清单内的歌曲”题头带上当前清单名，如 [舒缓音乐]。"""
        name = ""
        pid = getattr(self, "cur_playlist", None)
        for pl in getattr(self, "playlists", []) or []:
            if pl.get("playlistid") == pid:
                name = str(pl.get("name", ""))
                break
        prefix = f" [{name}] " if name else " "
        for tree in (getattr(self, "pl_songs", None), getattr(self, "pl_songs2", None)):
            if tree is None:
                continue
            lf = tree.master
            try:
                text = str(lf.cget("text"))
                base_text = text.split("清单内的歌曲", 1)[-1]
                if not base_text.startswith("（"):
                    base_text = "（拖到播放控制页的播放队列可加入）"
                lf.configure(text=f"{prefix}清单内的歌曲{base_text}")
            except Exception:
                pass

    def action_find_lib(self, tree=None):
        """v42：以实际焦点/指定树为搜索源，再同步另一套，避免搜到主表去。"""
        main = getattr(self, "lib_tree", None)
        second = getattr(self, "lib_tree2", None)
        focus = getattr(self, "_last_focus_tree", None)
        if tree is None:
            tree = focus if focus in (main, second) and focus is not None else main
        if tree is None:
            return
        self._call_with_tree("lib_tree", tree, super().action_find_lib)
        if main is None or second is None or not second.winfo_exists():
            return
        for target in (main, second):
            if target is tree:
                continue
            for iid in tree.selection():
                if target.exists(iid):
                    target.selection_set(iid)
                    target.see(iid)
        if focus is second:
            try:
                second.focus_set()
            except Exception:
                pass

    def _call_with_tree(self, attr, tree, fn, *args, **kwargs):
        """让复用主套控件的回调临时看到副套树（按钮点击时不在事件派发里）。"""
        old = getattr(self, attr)
        setattr(self, attr, tree)
        try:
            return fn(*args, **kwargs)
        finally:
            setattr(self, attr, old)

    def on_lib_context(self, event):
        """v42：右键菜单命令要绑定触发它的那棵树，不能等弹出后读主套引用。"""
        tree = getattr(event, "widget", None)
        if tree not in (getattr(self, "lib_tree", None),
                        getattr(self, "lib_tree2", None)):
            tree = getattr(self, "lib_tree", None)
        if tree is None:
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        if iid not in tree.selection():
            tree.selection_set(iid)
        sel = self._call_with_tree("lib_tree", tree, self._sel_lib)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(
            label=f"修改（{len(sel)} 首）" if len(sel) > 1 else "修改曲目信息",
            command=lambda t=tree: self._call_with_tree(
                "lib_tree", t, self.action_edit_track))
        m.add_command(label="同步到播放器",
                      command=lambda t=tree: self._call_with_tree(
                          "lib_tree", t, self.action_sync_selected))
        m.add_separator()
        m.add_command(label="加入播放清单…",
                      command=lambda t=tree: self._call_with_tree(
                          "lib_tree", t, self.action_add_to_playlist))
        sub = tk.Menu(m, tearoff=0)
        for pl in self.playlists:
            pid, name = pl.get("playlistid"), pl.get("name", "未命名")
            sub.add_command(
                label=name,
                command=lambda p=pid, n=name, t=tree: self._call_with_tree(
                    "lib_tree", t, self.action_add_to_specific, p, n))
        m.add_cascade(label="增加至 ▶", menu=sub)
        m.add_separator()
        m.add_command(
            label=f"删除（{len(sel)} 首）",
            command=lambda t=tree: self._call_with_tree(
                "lib_tree", t, self.action_delete_tracks))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def on_playlist_context(self, event):
        """v42：播放清单右键命令同样绑定事件来源树（主套/副套）。"""
        tree = getattr(event, "widget", None)
        if tree not in (getattr(self, "pl_tree", None),
                        getattr(self, "pl_tree2", None)):
            tree = getattr(self, "pl_tree", None)
        if tree is None:
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        tree.selection_set(iid)
        m = tk.Menu(self.root, tearoff=0)
        for label, fn in (("改名", self.action_rename_playlist),
                          ("刷新成员", self.action_refresh_pl_members),
                          ("删除列表", self.action_delete_playlist)):
            m.add_command(label=label,
                          command=lambda t=tree, f=fn: self._call_with_tree(
                              "pl_tree", t, f))
            if label == "刷新成员":
                m.add_separator()
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _render_lib(self):
        """v40：重绘曲库后恢复两套树的选中行，否则删除/改名按钮会读不到选中。"""
        keep = {}
        for name in ("lib_tree", "lib_tree2"):
            tree = getattr(self, name, None)
            if tree is not None:
                keep[name] = tuple(tree.selection())
        super()._render_lib()
        focus = getattr(self, "_last_focus_tree", None)
        for name, iids in keep.items():
            tree = getattr(self, name, None)
            if tree is None or not iids:
                continue
            for iid in iids:
                if tree.exists(iid):
                    tree.selection_add(iid)
        if any(keep.values()):
            self._apply_focus_highlight(focus)

    def on_playlist_select(self, event):
        """v41：选中清单只在“首次选中/显式刷新”时读设备一次，避免两侧互相轮询。"""
        tree = getattr(self, "pl_tree", None)
        sel = list(tree.selection()) if tree is not None else []
        if not sel or not sel[0].startswith("pl::"):
            return
        plid = int(sel[0][4:])
        self.cur_playlist = plid
        ids = list(self.pl_members.get(plid, []))
        byid = {t.get("trackid"): t for t in getattr(self, "tracks", [])}
        self.pl_tracks = [byid[m] for m in ids if m in byid]
        self._render_pl_songs()
        if getattr(self, "_pl_restore_guard", False):
            return
        checked = getattr(self, "_pl_checked_members", None)
        if checked is None:
            checked = self._pl_checked_members = set()
        if plid in checked:
            return
        checked.add(plid)     # 先占位：重绘恢复选中产生的二次事件不会重复入队

        def fn(d):
            members = d.get_playlist_tracks(plid)
            self.q.put(("pl_members", plid, members))
            self._log(f"[OK] 播放清单成员已从设备读取：{len(members)} 首。")

        fn._safe_retry = True
        self._worker(fn)

    def action_refresh_pl_members(self):
        """右键“刷新成员”才是强制重读，普通选中不读第二遍。"""
        plid = getattr(self, "cur_playlist", None)
        if plid is not None:
            getattr(self, "_pl_checked_members", set()).discard(plid)
        super().action_refresh_pl_members()

    def _render_playlists(self):
        """v40/v41：重绘后恢复两套树的选中行；恢复过程不再触发设备读取。"""
        keep = {}
        for name in ("pl_tree", "pl_tree2"):
            tree = getattr(self, name, None)
            if tree is not None:
                keep[name] = tuple(tree.selection())
        self._pl_restore_guard = True
        try:
            super()._render_playlists()
            focus = getattr(self, "_last_focus_tree", None)
            for name, iids in keep.items():
                tree = getattr(self, name, None)
                if tree is None or not iids:
                    continue
                for iid in iids:
                    if tree.exists(iid):
                        tree.selection_add(iid)
            self._sync_playlist_song_titles()
            if any(keep.values()):
                self._apply_focus_highlight(focus)
        finally:
            self._pl_restore_guard = False

    def _render_pl_songs(self, sorted_tracks=None, only_tree=None):
        """v39：清单内歌曲渲染 6 列（含专辑/流派）；支持表头排序。"""
        tracks = sorted_tracks if sorted_tracks is not None else getattr(self, "pl_tracks", [])
        rows = []
        for i, t in enumerate(tracks):
            rows.append((f"ps::{i}", (
                i + 1,
                t.get("title", ""), t.get("artist", ""),
                t.get("album", ""), t.get("genre", ""),
                t.get("trackid", ""))))
        targets = []
        if only_tree is not None:
            targets.append(only_tree)
        else:
            for name in ("pl_songs", "pl_songs2"):
                tree = getattr(self, name, None)
                if tree is not None:
                    targets.append(tree)
        keep = {id(tree): tuple(tree.selection()) for tree in targets
                if tree is not None}
        for tree in targets:
            try:
                if not tree.winfo_exists():
                    continue
                tree.delete(*tree.get_children())
                for iid, vals in rows:
                    tree.insert("", "end", iid=iid, values=vals)
                for iid in keep.get(id(tree), ()):
                    if tree.exists(iid):
                        tree.selection_add(iid)
            except Exception:
                pass
        if only_tree is None:
            self._sync_playlist_song_titles()

    def _pl_focus_out(self, tree):
        """播放清单失去焦点：选行改淡黄底，提示仍然选中。"""
        if getattr(self, "_last_focus_tree", None) is tree:
            self._last_focus_tree = None
        self._apply_focus_highlight(None)

    def _apply_focus_highlight(self, focus_tree=None):
        """v36：播放清单选中行失焦后也保持淡黄底（与播控队列一致）。"""
        super()._apply_focus_highlight(focus_tree)
        for t, iid in getattr(self, "_pl_warm_rows", []):
            try:
                if t.winfo_exists() and t.exists(iid):
                    t.item(iid, tags=tuple(x for x in t.item(iid, "tags") if x != "warm"))
            except Exception:
                pass
        touched = []
        for tree in (getattr(self, "pl_tree", None), getattr(self, "pl_tree2", None)):
            if tree is None or not tree.winfo_exists() or tree is focus_tree:
                continue
            tags = base.skin_cfg.focus_tags(getattr(self, "skin", None))
            tree.tag_configure("warm", background=tags["warm"][0],
                               foreground=tags["warm"][1])
            for iid in tree.selection():
                try:
                    old = [x for x in tree.item(iid, "tags") if x != "warm"]
                    tree.item(iid, tags=tuple(old + ["warm"]))
                    touched.append((tree, iid))
                except Exception:
                    pass
        self._pl_warm_rows = touched

    def action_seek(self):
        """v40：定位后把内部计时基准一并刷成实际播放位置。"""
        super().action_seek()
        try:
            sec = int(float(self.play_pos_var.get()))
            self.play_elapsed_sec = float(sec)
            self.play_seek_at = time.monotonic()
            self.play_last_sync = time.monotonic()
            # seek 造成 elapsed 跳变，不作为差速样本。
            self._rate_last_elapsed = None
            self._rate_last_t = None
            self._update_position_title(sec)
        except Exception:
            pass

    def _update_position_title(self, sec=None):
        """播放位置由控制工作台的播放控制框体标题承载。"""
        try:
            super()._update_position_title(sec)
        except Exception:
            pass

    # ------------------------------------------------- v41：播放差速补偿 / 时长锁定
    def _calibrate_play_sync(self, ev):
        """把设备回报换算成“软件正在播的这一首”，并采集差速样本。

        两个真机事实：
          1. 播放接口只回【队列位置 + 已播秒数】，不回曲目 ID；我们逐曲
             play_track 时设备队列位置可能还停在上一首，基类据此取曲目会把
             总时长来回切成 00:43 / 00:50 / 00:53。
          2. 设备固件每秒计数与 PC 的 tick 不完全同速，只按 +1 模拟会在曲末
             提前判“播完”。这里用连续几次同步的 (Δ设备秒数 / ΔPC 时间)
             估算补偿系数，并做 EMA 平滑。
        """
        if not isinstance(ev, tuple) or len(ev) < 3:
            return ev
        if len(ev) >= 4:
            # 兼容旧四元签名 (kind, st, elapsed, queue_pos)。
            self._track_play_rate({"elapsed": ev[2]})
            return (ev[0], ev[1], ev[2], 0)
        obs = dict(ev[1]) if isinstance(ev[1], dict) else {}
        adv = dict(ev[2]) if isinstance(ev[2], dict) else {}
        cur = getattr(self, "_play_stage_index", None)
        # 逐曲模式下软件认定的曲目就是真相源：把设备位置重定向到当前曲目，
        # 基类就不会用陈旧队列位置取到上一首的 length（日志里时长来回跳）。
        if getattr(self, "_device_order", None) and isinstance(cur, int):
            stage = getattr(self, "stage_tracks", []) or []
            if 0 <= cur < len(stage):
                t = stage[cur]
                obs["queue_pos"] = cur
                self.play_track_info = t
                self.play_track_id = t.get("trackid")
                self.play_queue_pos = cur
            else:
                obs["queue_pos"] = 0
            adv["advanced"] = False
        self._track_play_rate(obs)
        return (ev[0], obs, adv)

    def _track_play_rate(self, obs):
        """按设备同步回的 elapsed 变化率估算 PC/播放器差速（0.6~1.6 倍）。"""
        elapsed = obs.get("elapsed")
        if getattr(self, "play_state", None) != "playing" or not isinstance(
                elapsed, (int, float)) or elapsed < 0:
            return
        now = time.monotonic()
        cur = getattr(self, "_play_stage_index", None)
        if getattr(self, "_rate_track", None) != cur:
            self._rate_track = cur
            self._rate_last_elapsed = None
            self._rate_last_t = None
            # v43：跨曲沿用已学倍率；只有从未学过（None）才用 1.0 起步。
            if getattr(self, "_play_rate", None) is None:
                self._play_rate = 1.0
        last_e = getattr(self, "_rate_last_elapsed", None)
        last_t = getattr(self, "_rate_last_t", None)
        if (last_e is not None and last_t is not None
                and elapsed >= last_e and 0.5 <= now - last_t <= 15):
            instant = (elapsed - last_e) / (now - last_t)
            if 0.5 <= instant <= 2.0:
                smooth = getattr(self, "_play_rate", 1.0) * 0.7 + instant * 0.3
                self._play_rate = round(max(0.6, min(1.6, smooth)), 3)
        self._rate_last_elapsed = elapsed
        self._rate_last_t = now

    def _tick_playback(self):
        """v44：本地计时每秒 +1（真机已证与设备 1:1），到 total 由基类的
        ended 路径【单一触发】曲终调度。

        v43 曾在这里叠差速补偿、300ms 高频对齐、0.35s 提前量，真机实测
        （43s 曲目归零 43.9s、play_track 立即切歌）证明全是多余，且
        “提前量触发一次 + 到 total 再触发一次”正是曲末乱跳的根因，全部撤销。
        """
        super()._tick_playback()

    def _tick_tail_align(self):
        """v44：已停用。保留空实现兼容旧调用，绝不再重复触发曲终调度。"""
        return

    # -------------------------------------------------------------- 设备状态一行
    def _layout_info_bar(self):
        """右侧单行排布：容量 / 可用 / 电源 / 固件；左侧为音量与 EAX。"""
        right = getattr(self, "info_right", None)
        labels = getattr(self, "info_labels", {})
        if right is None or not labels:
            return
        for w in right.winfo_children():
            w.grid_forget()
        for i, key in enumerate(("cap", "free", "bat", "fw")):
            if key in labels:
                labels[key].grid(row=0, column=i, sticky="w", padx=(0, 14), pady=0)

    # -------------------------------------------------------------- 传输工作台左右缩进（竖线+三角）
    def _pin_folded_sash(self, paned, sash_index, pin_px, is_folded):
        """v38：折叠侧的 sash 钉死为固定窄条，禁止拖动改变大小。
        折叠期间每 120ms 强制回位，抵消 dock 重排/主题切换带来的 sash 重置。"""
        if paned is None:
            return

        def _enforce():
            try:
                if paned.winfo_exists() and is_folded():
                    px = pin_px() if callable(pin_px) else pin_px
                    if paned.sashpos(sash_index) != px:
                        paned.sashpos(sash_index, px)
            except Exception:
                pass
            # 只要处于折叠态就继续轮询；展开后自动停止。
            try:
                if is_folded():
                    self.root.after(120, _enforce)
            except Exception:
                pass

        def _press(e):
            # 折叠时点击 sash 不进入拖动：吞掉事件（返回 break）。
            if is_folded():
                _enforce()
                return "break"
            return None

        paned.bind("<ButtonPress-1>", _press, add="+")
        paned.bind("<B1-Motion>", lambda e: _enforce() if is_folded() else None, add="+")
        paned.bind("<ButtonRelease-1>", lambda e: _enforce() if is_folded() else None, add="+")
        paned.bind("<Configure>", lambda e: _enforce(), add="+")
        self.root.after(40, _enforce)
        self.root.after(250, _enforce)

    def _push_workbench_sash(self):
        def _apply():
            p = getattr(self, "workbench_paned", None)
            if p is None:
                return
            try:
                total = p.winfo_width()
                if total < 80:
                    return
                st = getattr(self, "workbench_collapsed", {})
                left = 14 if st.get("explorer") else int(total * 0.28)
                right = total - 14 if st.get("transfer") else int(total * 0.72)
                p.sashpos(0, left)
                p.sashpos(1, right)
                mp = getattr(self, "machine_paned", None)
                if mp is not None and mp.winfo_width() > 200:
                    mp.sashpos(0, int(mp.winfo_width() * 0.62))
            except Exception:
                pass
        self.root.after(30, _apply)

    def _make_wb_handle(self, side):
        """缩进后只留一道竖灰线 + 中间三角箭头按钮（与播控侧栏同款）。"""
        host = self.wb_ex if side == "explorer" else self.wb_tr
        p = self.pal
        line = tk.Frame(host, bg=p["border"], width=2)
        if side == "explorer":
            line.place(relx=1.0, rely=0, x=-3, width=2, relheight=1.0)
            arrow = "▶"
        else:
            line.place(relx=0.0, rely=0, x=1, width=2, relheight=1.0)
            arrow = "◀"
        cmd = self.action_explorer_fold if side == "explorer" else self.action_transfer_fold
        # v38：箭头按钮无底色（与栏背景一致），仅保留箭头本身。
        bg = p["bg"]
        btn = tk.Button(host, text=arrow, width=2, bd=0, relief="flat",
                        bg=bg, fg=p.get("idle_fg", p["text"]),
                        activebackground=bg, activeforeground=p["accent"],
                        highlightthickness=0, command=cmd)
        btn.place(relx=0.5, rely=0.5, anchor="center")
        btn.bind("<Enter>", lambda e, b=btn: b.configure(
            fg=p["accent"]))
        btn.bind("<Leave>", lambda e, b=btn: b.configure(
            fg=p.get("idle_fg", p["text"])))
        self._wb_handles[side] = (line, btn)

    def _remove_wb_handle(self, side):
        pair = self._wb_handles.pop(side, None)
        if pair:
            for w in pair:
                try:
                    w.destroy()
                except Exception:
                    pass

    def _hide_labelwidget_children(self, title, hide):
        """v38：折叠时连 title（labelwidget）内容一起清空，像播控侧栏一样为空。"""
        if title is None:
            return
        try:
            if hide:
                self._ex_title_children = [
                    (w, w.pack_info()) for w in title.winfo_children()
                    if w.winfo_ismapped() or w.winfo_manager() == "pack"]
                for w, _info in self._ex_title_children:
                    w.pack_forget()
            else:
                for w, info in getattr(self, "_ex_title_children", []):
                    try:
                        if w.winfo_exists():
                            w.pack(**info)
                    except Exception:
                        pass
                self._ex_title_children = []
        except Exception:
            pass

    def action_explorer_fold(self):
        st = self.workbench_collapsed
        collapsed = not st.get("explorer", False)
        body, title = getattr(self, "explorer_body", None), getattr(self, "explorer_title", None)
        # 标题行是 LabelFrame 的 labelwidget，始终由框体自己排版，不能 pack_forget/place。
        if collapsed:
            if body is not None:
                body.pack_forget()
            self._hide_labelwidget_children(title, True)
            self._make_wb_handle("explorer")
        else:
            self._remove_wb_handle("explorer")
            self._hide_labelwidget_children(title, False)
            if body is not None:
                body.pack(fill="both", expand=True)
        st["explorer"] = collapsed
        self.workbench_collapsed = st
        btn = getattr(self, "explorer_fold_btn", None)
        if btn is not None and btn.winfo_exists():
            btn.configure(text="»" if collapsed else "«")
        self._push_workbench_sash()

    def action_transfer_fold(self):
        st = self.workbench_collapsed
        collapsed = not st.get("transfer", False)
        body, head = getattr(self, "transfer_body", None), getattr(self, "transfer_head", None)
        if collapsed:
            if body is not None:
                body.pack_forget()
            if head is not None:
                head.pack_forget()
            self._make_wb_handle("transfer")
        else:
            self._remove_wb_handle("transfer")
            if head is not None:
                head.pack(fill="x")
            if body is not None:
                body.pack(fill="both", expand=True)
        st["transfer"] = collapsed
        self.workbench_collapsed = st
        btn = getattr(self, "transfer_fold_btn", None)
        if btn is not None and btn.winfo_exists():
            btn.configure(text="«" if collapsed else "»")
        self._push_workbench_sash()

    def _bind_fullwidth_title(self, labelframe, title_frame, pad=10):
        """LabelFrame 的 labelwidget 默认只包住标题文字；绑定尺寸变化让它撑满
        整行宽度，内部 pack(side="right") 的折叠箭头即可贴到最右侧。"""
        # pack_propagate(False) 后子控件不再撑高容器，必须显式给高度，
        # 否则标题文字/按钮被裁剪看不见。
        try:
            title_frame.configure(height=24)
        except Exception:
            pass

        def apply():
            try:
                if not title_frame.winfo_exists() or not labelframe.winfo_exists():
                    return
                w = max(120, labelframe.winfo_width() - pad)
                if abs(title_frame.winfo_width() - w) > 2:
                    title_frame.configure(width=w)
            except Exception:
                pass
        try:
            # add="+" 必须保留：否则会覆盖 LabelFrame 已有的 Configure 绑定，
            # 导致内部列表/标题渲染异常（播放清单标题消失）。
            labelframe.bind("<Configure>", lambda e: apply(), add="+")
        except Exception:
            pass
        self.root.after(60, apply)
        self.root.after(400, apply)

    # -------------------------------------------------------------- 资源管理器（精简版）
    def _build_explorer_panel(self, parent):
        """去掉加入文件夹MP3/路径输入框/底部状态文字，目录/文件行贴顶。"""
        super()._build_explorer_panel(parent)
        # v39：资源管理器文件列表表头可点击排序 + 排序箭头。
        try:
            for c, h in (("name", "名称"), ("size", "大小"),
                         ("type", "类型"), ("mtime", "修改时间")):
                self.file_tree.heading(
                    c, text=h, command=lambda cc=c: self.action_sort_file(cc))
        except Exception:
            pass
        # v36：标题行直接作为 LabelFrame 的 labelwidget，贴顶且不再有 19px 空标题留白。
        self.explorer_frame.configure(text="")
        ex_title = self.explorer_fold_btn.master
        self.explorer_title = ex_title
        ex_title.pack_forget()
        self.explorer_frame.configure(labelwidget=ex_title)
        self.explorer_fold_btn.configure(text="«")
        self.explorer_fold_btn.pack(side="right", padx=(4, 0))
        # v37：标题行撑满框宽，让折叠箭头「«」真正靠到最右侧。
        ex_title.pack_propagate(False)
        self._bind_fullwidth_title(self.explorer_frame, ex_title)
        for w in ex_title.winfo_children():
            if isinstance(w, ttk.Label):
                w.configure(text="资源管理器 目录 / 文件", wraplength=0)
        # 删除底部“路径输入框 + 加入此文件夹 MP3”条与状态文字
        for w in list(self.explorer_body.winfo_children()):
            if isinstance(w, ttk.Label) and "textvariable" in w.keys():
                w.destroy()
            elif isinstance(w, ttk.Frame):
                if any("加入此文件夹" in str(c.cget("text"))
                       for c in w.winfo_children() if "text" in c.keys()):
                    w.destroy()

    def action_add_folder(self, path=None):
        """新版已取消“加入此文件夹 MP3”功能，保留空实现兼容旧引用。"""
        return

    def on_file_context(self, event):
        iid = self.file_tree.identify_row(event.y)
        if iid:
            self.file_tree.selection_set(iid)
        paths = self._selected_file_paths()
        m = tk.Menu(self.root, tearoff=0)
        if paths:
            m.add_command(label="加入传输列表",
                          command=lambda: self._add_files_to_queue(paths))
        m.add_command(label="刷新", command=lambda: self.on_folder_select(None))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    @staticmethod
    def _no_label_space(lf):
        """用 1px 的 labelwidget 消除 ttk.LabelFrame 空标题的 19px 留白。"""
        tiny = ttk.Frame(lf, width=1, height=1)
        lf.configure(text="", labelwidget=tiny)
        return tiny

    @staticmethod
    def _sort_key_text(value):
        """英文大小写一起排（aA 相邻），中文/其它 Unicode 排在英文之后。"""
        s = str(value or "").casefold()
        return (0 if s.isascii() else 1, s)

    def _ensure_children(self, iid):
        """懒加载子目录；排序改为英文不区分大小写在前、中文在后。"""
        if not iid.startswith("path::") or iid.endswith("::dummy"):
            return
        if iid in getattr(self, "_loaded_dirs", set()):
            return
        path = iid[len("path::"):]
        self.folder_tree.delete(*self.folder_tree.get_children(iid))
        try:
            names = sorted(os.listdir(path), key=self._sort_key_text)
        except Exception:
            names = []
        for d in names:
            sub = os.path.join(path, d)
            if not os.path.isdir(sub):
                continue
            ci = "path::" + sub
            if self.folder_tree.exists(ci):
                continue
            self.folder_tree.insert(iid, "end", iid=ci, text=d,
                                    values=(sub,), tags=("folder",))
            self._add_dummy(sub, ci)
        self._loaded_dirs.add(iid)

    def action_sort_file(self, col):
        """资源管理器表头点击排序：同列切换升/降序，并更新箭头。"""
        cur = getattr(self, "_file_sort", None) or ("__none__", True)
        asc = (not cur[1]) if cur[0] == col else True
        self._file_sort = (col, asc)
        heads = {"name": "名称", "size": "大小", "type": "类型", "mtime": "修改时间"}
        for c, txt in heads.items():
            try:
                self.file_tree.heading(c, text=txt)
            except Exception:
                pass
        self._mark_sort_arrow(self.file_tree, col, asc)
        # 用当前目录重排
        cur_dir = getattr(self, "_current_dir", None)
        if cur_dir is None:
            sel = getattr(self, "folder_tree", None)
            if sel is not None and sel.selection():
                iid = sel.selection()[0]
                if str(iid).startswith("path::"):
                    cur_dir = str(iid)[6:]
        if cur_dir and os.path.isdir(cur_dir):
            self._populate_file_list(cur_dir)

    def _mark_file_arrow(self, col, asc):
        heads = {"name": "名称", "size": "大小", "type": "类型", "mtime": "修改时间"}
        try:
            self.file_tree.heading(col, text=f"{heads.get(col, col)}  {'↓' if asc else '↑'}")
        except Exception:
            pass

    def _populate_file_list(self, path: str):
        """文件栏：默认文件夹在前+英文不敏感；点表头后按该列升/降序。"""
        if not hasattr(self, "file_tree"):
            return
        self._current_dir = path
        for i in self.file_tree.get_children():
            self.file_tree.delete(i)
        try:
            entries = list(os.scandir(path))
        except Exception as exc:
            self.explorer_status_var.set(f"无法读取：{exc}")
            return
        sort = getattr(self, "_file_sort", None) or ("name", True)
        col, asc = sort

        def skey(e):
            if col == "name":
                return (not e.is_dir(), self._sort_key_text(e.name))
            if col == "size":
                try:
                    v = 0 if e.is_dir() else e.stat().st_size
                except Exception:
                    v = 0
                return v
            if col == "type":
                typ = "文件夹" if e.is_dir() else (
                    "MP3 音频" if e.name.lower().endswith(".mp3") else "文件")
                return self._sort_key_text(typ)
            if col == "mtime":
                try:
                    return e.stat().st_mtime
                except Exception:
                    return 0
            return self._sort_key_text(e.name)
        try:
            entries.sort(key=skey, reverse=not asc)
        except Exception:
            entries.sort(key=lambda e: (not e.is_dir(), self._sort_key_text(e.name)))
        for ent in entries:
            try:
                st = ent.stat()
                isdir = ent.is_dir()
                size = "" if isdir else f"{st.st_size / 1024:.0f} KB"
                typ = "文件夹" if isdir else ("MP3 音频" if ent.name.lower().endswith(".mp3") else "文件")
                mt = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
                self.file_tree.insert("", "end", iid=ent.path,
                                      values=(ent.name, size, typ, mt, ent.path),
                                      tags=("mp3",) if typ == "MP3 音频" else ())
            except Exception:
                continue
        self.explorer_status_var.set(f"{path}  ·  {len(self.file_tree.get_children())} 项")

    def on_explorer_double(self, event):
        """目录树双击展开/收起：延迟到原生箭头处理之后再落定最终状态。"""
        t = self.folder_tree
        iid = t.identify_row(event.y)
        if not iid:
            return
        if iid.endswith("::dummy"):
            iid = iid[:-len("::dummy")]
        if iid == "__pc__" or iid.startswith("path::"):
            self._ensure_children(iid)
            t.selection_set(iid)
            try:
                opened = bool(int(t.item(iid, "open")))
            except Exception:
                opened = False
            # 原生 Treeview 对箭头区的两次点击各切换一次；after_idle 保证我们最后写一次。
            def apply(target=iid, want=not opened):
                try:
                    if t.winfo_exists() and t.exists(target):
                        t.item(target, open=want)
                except Exception:
                    pass
            self.root.after_idle(apply)
        elif iid.startswith("file::"):
            self._add_files_to_queue([iid[6:]])

    @staticmethod
    def _chain_has(widget, target):
        w = widget
        while w is not None:
            if w is target:
                return True
            w = getattr(w, "master", None)
        return False

    def _drop_target_kind(self, widget):
        """识别文件释放位置：传输队列 / 机器内歌曲 / 播放清单内歌曲。"""
        queue_tree = getattr(self, "tree", None)
        groups = (
            ("queue", (queue_tree, getattr(queue_tree, "master", None))),
            ("lib2", (self.lib_tree2, self.lib_tree2.master)),
            ("pl2", (self.pl_songs2, self.pl_songs2.master,
                     self.pl_tree2, self.pl_tree2.master)),
            ("lib", (self.lib_tree, self.lib_tree.master)),
            ("pl", (self.pl_songs, self.pl_songs.master,
                    self.pl_tree, self.pl_tree.master)),
        )
        for kind, widgets in groups:
            if any(self._chain_has(widget, w) for w in widgets if w is not None):
                return kind
        return None

    def _selected_playlist_for(self, kind):
        """取当前选中的播放清单 (id, 名称)，两套清单树都支持。"""
        tree = self.pl_tree2 if kind == "pl2" else self.pl_tree
        plid = None
        try:
            sel = tree.selection() if tree.winfo_exists() is True else ()
        except Exception:
            sel = ()
        if sel and str(sel[0]).startswith("pl::"):
            try:
                plid = int(str(sel[0])[4:])
            except (TypeError, ValueError):
                plid = None
        if plid is None:
            plid = getattr(self, "cur_playlist", None)
        name = ""
        if plid is not None:
            for pl in getattr(self, "playlists", []) or []:
                if pl.get("playlistid") == plid:
                    name = str(pl.get("name", ""))
                    break
            if not name:
                name = str(plid)
        return plid, name

    def _playlist_name(self, plid):
        for pl in getattr(self, "playlists", []) or []:
            if pl.get("playlistid") == plid:
                return str(pl.get("name", ""))
        return str(plid) if plid is not None else ""

    def _drop_playlist_id(self, widget, x_root=None, y_root=None):
        """拖到清单名字行时取该行 ID；拖到清单歌曲区时取当前选中清单。"""
        if x_root is None or y_root is None:
            x_root, y_root = self.root.winfo_pointerxy()
        pairs = (("pl2", getattr(self, "pl_tree2", None), getattr(self, "pl_songs2", None)),
                 ("pl", getattr(self, "pl_tree", None), getattr(self, "pl_songs", None)))
        for kind, tree, songs in pairs:
            if tree is None or not (self._chain_has(widget, tree) or
                                    (songs is not None and self._chain_has(widget, songs))):
                continue
            if self._chain_has(widget, tree):
                try:
                    ty = y_root - tree.winfo_rooty()
                    iid = tree.identify_row(max(0, ty))
                    if iid and str(iid).startswith("pl::"):
                        return int(str(iid)[4:]), self._playlist_name(int(str(iid)[4:]))
                except Exception:
                    pass
            return self._selected_playlist_for(kind)
        return None, ""

    def on_file_drag_start(self, event):
        super().on_file_drag_start(event)
        popup = getattr(self, "_drag_popup", None)
        if popup is not None:
            n = len(getattr(self, "_drag_paths", []) or [])
            for w in popup.winfo_children():
                if "text" in w.keys():
                    w.configure(text=f"拖到机器内歌曲 / 清单内歌曲释放（{n} 首）")

    def on_file_drag_release(self, event):
        """文件拖到曲库列表=加入传输队列；拖到清单内歌曲=同时预存目标清单。"""
        if not getattr(self, "_drag_active", False):
            return
        self._drag_active = False
        popup = getattr(self, "_drag_popup", None)
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass
        self._drag_popup = None
        paths = list(getattr(self, "_drag_paths", []) or [])
        self._drag_paths = []
        if not paths:
            return
        target = self.root.winfo_containing(event.x_root, event.y_root)
        kind = self._drop_target_kind(target)
        if kind is None:
            self._log("[!] 文件未放入歌曲列表：请拖到“机器内歌曲”或“清单内的歌曲”。")
            return
        before = len(self.queue_rows)
        self._add_files_to_queue(paths)
        added = len(self.queue_rows) - before
        if kind in ("pl2", "pl"):
            plid, name = self._drop_playlist_id(target, event.x_root, event.y_root)
            for row in self.queue_rows[before:]:
                row["target_pl_id"] = plid
                row["target_pl_name"] = name
            self._render_queue()
            if plid is not None:
                self._log(f"[+] {added} 首已加入传输队列，传输后写入播放清单【{name}】。")
            else:
                self._log(f"[+] {added} 首已加入传输队列（未选中播放清单，无目标清单）。")
        else:
            self._log(f"[+] {added} 首已加入传输队列。")

    def on_explorer_context(self, event):
        iid = self.folder_tree.identify_row(event.y)
        if not iid:
            return
        self.folder_tree.selection_set(iid)
        m = tk.Menu(self.root, tearoff=0)
        if iid.startswith("path::"):
            m.add_command(label="打开（展开）", command=lambda: (
                self._ensure_children(iid), self.folder_tree.item(iid, open=True)))
            m.add_command(label="刷新", command=lambda: self.reload_folder(iid))
        elif iid.startswith("file::"):
            m.add_command(label="加入传输列表",
                          command=lambda: self._add_files_to_queue([iid[6:]]))
        else:
            m.add_command(label="刷新", command=self._init_folder_tree)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # -------------------------------------------------------------- 传输工作台：中间机器曲库
    def _build_machine_tab(self, parent):
        super()._build_machine_tab(parent)
        # v39：传输页“清单内的歌曲”加 专辑/流派 两列 + 表头排序。
        self._upgrade_pl_songs_columns(getattr(self, "pl_songs2", None))
        p = self.pal
        left = self.lib_tree2.master.master
        lf2 = self.lib_tree2.master
        sbar = self.lib_search_bar2(left)
        # v34：把“机器内歌曲（点表头排序…）”单独做成一行，放到搜索条上方；
        # 列表框体本身回到搜索条下方。
        title2 = ttk.Frame(left)
        title2.pack(fill="x", before=sbar)
        ttk.Label(title2, text=" 机器内歌曲（点表头排序 · 双击修改） ",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self._no_label_space(lf2)     # v36：标题已单独一行，框体自身不再留空位
        lf2.pack_forget()
        lf2.pack(fill="both", expand=True, before=self.lib_action_bar2(left))
        # 左栏搜索行：“定位”改“搜索”，并绑定到本页 lib_tree2
        for w in sbar.winfo_children():
            txt = str(w.cget("text")) if isinstance(w, ttk.Button) else ""
            if isinstance(w, ttk.Button) and ("定位" in txt or "搜索" in txt):
                w.configure(text="搜索",
                            command=lambda t=self.lib_tree2: self.action_find_lib(t))
        # 右侧播放清单下方四个按钮收窄（仅传输工作台，保留功能）
        pbar = self.pl_tree2.master.winfo_children()[-1]
        for w in pbar.winfo_children():
            if isinstance(w, ttk.Button):
                w.configure(width=5)
        # 删除“打开「播放控制」工作台”按钮，空间让出来
        right = self.pl_songs2.master.master
        for w in list(right.winfo_children()):
            if isinstance(w, ttk.Button) and "播放控制" in str(w.cget("text")):
                w.destroy()
        # v34：播放清单 / 清单内歌曲上下可拖拽，且各自有最小高度。
        right.configure(width=260)   # 子控件改为 place 后需显式给面板一个请求宽度
        pf2, pit2 = self.pl_tree2.master, self.pl_songs2.master
        self.transfer_playlist_split = self._vertical_split(
            right, pf2, pit2, min_top=92, min_bottom=120, ratio=0.42)
        # v40：底部四按钮的“选中曲目”必须来自本页 lib_tree2；
        # 播放清单的 改名/删除 必须来自本页 pl_tree2。原基类回调读主套控件，
        # 因此这里用同款临时引用切换包一层，避免串到播控工作台。
        lbar = self.lib_action_bar2(left)
        if lbar is not None:
            for w in lbar.winfo_children():
                if not isinstance(w, ttk.Button):
                    continue
                txt = str(w.cget("text"))
                if "下载选中" in txt:
                    w.configure(command=lambda: self._call_with_tree(
                        "lib_tree", self.lib_tree2, self.action_download))
                elif "删除选中" in txt:
                    w.configure(command=lambda: self._call_with_tree(
                        "lib_tree", self.lib_tree2, self.action_delete_tracks))
                elif "改元数据" in txt or "改名" in txt:
                    w.configure(command=lambda: self._call_with_tree(
                        "lib_tree", self.lib_tree2, self.action_edit_track))
        for w in pbar.winfo_children():
            if not isinstance(w, ttk.Button):
                continue
            txt = str(w.cget("text"))
            if "改名" in txt:
                w.configure(command=lambda: self._call_with_tree(
                    "pl_tree", self.pl_tree2, self.action_rename_playlist))
            elif "删除" in txt:
                w.configure(command=lambda: self._call_with_tree(
                    "pl_tree", self.pl_tree2, self.action_delete_playlist))
        # v40：清单内歌曲的 移除已选 / 清空清单 放回传输工作台。
        self._add_pl_song_buttons(pit2, self.pl_songs2)

    @staticmethod
    def lib_search_bar2(left):
        """传输工作台左栏的搜索行（按内容识别，避免依赖 pack 顺序）。"""
        for w in left.winfo_children():
            if isinstance(w, ttk.Frame) and any(
                    isinstance(c, ttk.Entry) for c in w.winfo_children()):
                return w
        return left.winfo_children()[0]

    @staticmethod
    def lib_action_bar2(left):
        """传输工作台左栏曲库操作行（刷新曲库/下载选中…）。"""
        for w in left.winfo_children():
            if isinstance(w, ttk.Frame) and any(
                    isinstance(c, ttk.Button) and "刷新曲库" in str(c.cget("text"))
                    for c in w.winfo_children()):
                return w
        return None

    # ---- 机器内歌曲 → 播放清单（两套清单树都能接住） ---------------------
    def on_lib_drag_start(self, event):
        """v44：从事件来源树取拖动 ID。

        基类固定用 lib_tree（主套）识别行，副套树发起拖动时 y 坐标对不上，
        _pl_drag_ids 会落空 —— 这正是传输页“机器内歌曲”拖不进播控队列的根因。
        """
        src = getattr(event, "widget", None)
        self._pl_drag_src = src
        sel = []
        if src is not None:
            try:
                sel = [int(i) for i in src.selection() if str(i).isdigit()]
            except Exception:
                sel = []
        if not sel and getattr(self, "lib_tree", None) is not None:
            try:
                iid = self.lib_tree.identify_row(event.y)
                sel = [int(i) for i in self.lib_tree.selection() if str(i).isdigit()]
                if iid and iid.isdigit() and int(iid) not in sel:
                    sel = [int(iid)]
            except Exception:
                sel = []
        self._pl_drag_ids = sel

    def on_lib_drag_motion(self, event):
        if not getattr(self, "_pl_drag_ids", None):
            return
        wx, wy = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(wx, wy)
        src = getattr(self, "_pl_drag_src", None) or getattr(self, "lib_tree", None)
        src_is_second = src is getattr(self, "lib_tree2", None)
        pl_tree = self.pl_tree2 if src_is_second else self.pl_tree
        pl_songs = self.pl_songs2 if src_is_second else self.pl_songs
        hot = any(self._chain_has(widget, w) for w in
                  (pl_tree, pl_songs, pl_tree.master, pl_songs.master, self.stage_tree))
        self.root.configure(cursor="hand2" if hot else "")

    def on_lib_drag_release(self, event):
        self.root.configure(cursor="")
        ids = getattr(self, "_pl_drag_ids", None)
        self._pl_drag_ids = None
        if not ids:
            return
        wx, wy = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(wx, wy)
        # v44：拖进播控队列不需要先选中播放清单，先判定这个目标。
        if self._chain_has(widget, self.stage_tree):
            self._append_stage_ids(list(ids), "机器内歌曲")
            return
        if self.cur_playlist is None:
            return
        src = getattr(self, "_pl_drag_src", None) or getattr(self, "lib_tree", None)
        src_is_second = src is getattr(self, "lib_tree2", None)
        pl_tree = self.pl_tree2 if src_is_second else self.pl_tree
        pl_songs = self.pl_songs2 if src_is_second else self.pl_songs
        if not any(self._chain_has(widget, w) for w in
                   (pl_tree, pl_songs, pl_tree.master, pl_songs.master)):
            return
        trackids = [int(i) for i in ids if str(i).isdigit()]
        if not trackids:
            return
        plid, _name = self._drop_playlist_id(widget)
        if plid is None:
            plid = self.cur_playlist
        if plid is None:
            return

        def fn(d):
            # v37：合并清单原有成员后再写入，避免 copy-on-write 覆盖旧歌。
            newid, merged = self._append_playlist_keep_existing(d, plid, trackids)
            self._log(f"[OK] 已把 {len(trackids)} 首加入播放清单 ID {plid} → {newid}"
                      f"（清单现共 {len(merged)} 首）。")
            self.q.put(("pl_members_replace", plid, newid, merged))
            self.q.put(("playlist_retarget", plid, newid))
            self.q.put(("playlists", d.list_playlists()))

        self._worker(fn)

    def _vertical_split(self, host, top, bottom, min_top, min_bottom, ratio=0.42):
        """同父容器内的上下可拖拽分隔条（不重挂已有控件，避免被 PanedWindow 遮挡）。

        返回 sash 控件；比例保存在闭包 state 中，拖动时被 min_top/min_bottom 卡住。
        """
        p = getattr(self, "pal", {})
        sash = tk.Frame(host, height=4, bg=p.get("sash", p.get("border", "#555")),
                        cursor="sb_v_double_arrow")
        theme_sashes = getattr(self, "_theme_sashes", None)
        if theme_sashes is None:
            theme_sashes = self._theme_sashes = []
        theme_sashes.append(sash)
        st = {"ratio": ratio, "drag": None}
        S = 4   # v39：分隔条厚度由 8 收窄到 4，避免“大黑条”

        def apply(_event=None):
            h = host.winfo_height()
            if h <= min_top + min_bottom + S:
                return
            pair = h - S
            h1 = max(min_top, min(pair - min_bottom, int(pair * st["ratio"])))
            top.place(x=0, y=0, relwidth=1.0, height=h1)
            bottom.place(x=0, y=h1 + S, relwidth=1.0,
                         relheight=1.0, height=-(h1 + S))
            sash.place(x=0, y=h1, relwidth=1.0, height=S)
            st["ratio"] = h1 / pair

        def press(e):
            st["drag"] = (e.y_root, st["ratio"])

        def motion(e):
            d = st["drag"]
            if not d:
                return
            pair = host.winfo_height() - S
            if pair <= min_top + min_bottom:
                return
            y0, r0 = d
            h1 = max(min_top, min(pair - min_bottom,
                                  int(pair * r0) + (e.y_root - y0)))
            st["ratio"] = h1 / pair
            apply()

        def release(_e):
            st["drag"] = None

        top.pack_forget()
        bottom.pack_forget()
        sash.bind("<ButtonPress-1>", press)
        toplevel = host.winfo_toplevel()
        toplevel.bind("<B1-Motion>", motion, add="+")
        toplevel.bind("<ButtonRelease-1>", release, add="+")
        host.bind("<Configure>", apply, add="+")
        # 暴露给回归测试，避免合成鼠标事件受窗口位置影响。
        sash._split_apply = apply
        sash._split_press = press
        sash._split_motion = motion
        sash._split_release = release
        sash._split_state = st
        apply()
        return sash

    # -------------------------------------------------------------- 播控侧栏竖线 + 箭头
    def _pc_push_sash(self):
        def _apply():
            p = getattr(self, "pc_root", None)
            if p is None:
                return
            try:
                total = p.winfo_width()
                if total < 80:
                    return
                st = self.pc_side_collapsed
                left = 14 if st["left"] else int(total * 0.26)
                if st["right"]:
                    right = total - 14
                elif not st["left"]:
                    right = int(total * 0.78)
                else:
                    right = int(total * 0.62)
                p.sashpos(0, left)
                p.sashpos(1, right)
            except Exception:
                pass
        self.root.after(30, _apply)

    def _make_pc_collapse_handle(self, side):
        host = self.pc_left if side == "left" else self.pc_right
        p = self.pal
        line = tk.Frame(host, bg=p["border"], width=2)
        if side == "left":
            line.place(relx=1.0, rely=0, x=-3, width=2, relheight=1.0)
            arrow = "▶"
        else:
            line.place(relx=0.0, rely=0, x=1, width=2, relheight=1.0)
            arrow = "◀"
        cmd = self.action_pc_unfold_left if side == "left" else self.action_pc_unfold_right
        # v38：箭头按钮无底色，仅显示箭头。
        bg = p["bg"]
        btn = tk.Button(host, text=arrow, width=2, bd=0, relief="flat",
                        bg=bg, fg=p.get("idle_fg", p["text"]),
                        activebackground=bg, activeforeground=p["accent"],
                        highlightthickness=0, command=cmd)
        btn.place(relx=0.5, rely=0.5, anchor="center")
        btn.bind("<Enter>", lambda e, b=btn: b.configure(fg=p["accent"]))
        btn.bind("<Leave>", lambda e, b=btn: b.configure(
            fg=p.get("idle_fg", p["text"])))
        self._pc_handles[side] = (line, btn)

    def _remove_pc_collapse_handle(self, side):
        pair = self._pc_handles.pop(side, None)
        if pair:
            for w in pair:
                try:
                    w.destroy()
                except Exception:
                    pass

    def action_pc_fold_left(self):
        if getattr(self, "pc_side_collapsed", {}).get("left"):
            return
        # 播放清单与列表内歌曲同在 pc_left_body，一起缩（联动）
        self.pc_left_body.pack_forget()
        self.pc_side_collapsed["left"] = True
        self._make_pc_collapse_handle("left")
        self._pc_push_sash()

    def action_pc_unfold_left(self):
        if not getattr(self, "pc_side_collapsed", {}).get("left"):
            return
        self._remove_pc_collapse_handle("left")
        self.pc_left_body.pack(fill="both", expand=True)
        self.pc_side_collapsed["left"] = False
        self._pc_push_sash()

    def action_pc_fold_right(self):
        if getattr(self, "pc_side_collapsed", {}).get("right"):
            return
        self.pc_right_body.pack_forget()
        self.pc_side_collapsed["right"] = True
        self._make_pc_collapse_handle("right")
        self._pc_push_sash()

    def action_pc_unfold_right(self):
        if not getattr(self, "pc_side_collapsed", {}).get("right"):
            return
        self._remove_pc_collapse_handle("right")
        self.pc_right_body.pack(fill="both", expand=True)
        self.pc_side_collapsed["right"] = False
        self._pc_push_sash()

    # -------------------------------------------------------------- 播控工作台构建（后处理）
    def _build_playctrl_tab(self, parent):
        super()._build_playctrl_tab(parent)

        # v45：播控总开关直接写明单次播放模式建议，避免用户选用连播导致冲突。
        btn = getattr(self, "pcmode_btn", None)
        if btn is not None:
            btn.configure(
                text="清空机器内现有选定的音乐并开启软件控制的播控模式\n"
                     "（强烈建议NJB3使用单次播放模式）")

        # v39：播控页“清单内的歌曲”加 专辑/流派 两列 + 表头排序。
        self._upgrade_pl_songs_columns(getattr(self, "pl_songs", None))

        # v38：播控左右侧折叠后 sash 钉死为 14px，不能拖动放大。
        pr = getattr(self, "pc_root", None)
        if pr is not None:
            self._pin_folded_sash(
                pr, 0, 14,
                lambda: bool(getattr(self, "pc_side_collapsed", {}).get("left")))
            self._pin_folded_sash(
                pr, 1, lambda: max(14, pr.winfo_width() - 14),
                lambda: bool(getattr(self, "pc_side_collapsed", {}).get("right")))

        # ===== 左栏：标题行内放折叠按钮（最右），播放清单/清单内歌曲上下可拖 =====
        lb = self.pc_left_title.master
        pit = self.pl_songs.master
        pf = self.pl_tree.master
        lb.destroy()
        # 用自定义标题行覆盖 LabelFrame 的原生标题留白（不重挂已有控件）。
        pf.configure(text="")
        hdr = ttk.Frame(pf)
        pf.configure(labelwidget=hdr)   # v36：题头作 labelwidget，不再压在列表上/留空隙
        self.pc_left_fold_btn = ttk.Button(
            hdr, text="«", width=3, command=self.action_pc_fold_left)
        self.pc_left_fold_btn.pack(side="right", padx=(2, 0))
        ttk.Label(hdr, text=" 播放清单 ",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        # v37：标题行撑满框宽，折叠箭头「«」靠到最右侧。
        hdr.pack_propagate(False)
        self._bind_fullwidth_title(pf, hdr)
        # 删除播放清单底部四个按钮（新建/改名/刷新/删除）
        for w in list(pf.winfo_children()):
            if isinstance(w, ttk.Frame) and w is not hdr:
                w.destroy()
        # 同父容器内的上下拖拽分隔条：两个框体都能拖，且不会缩到看不见内容。
        self.pc_left_split = self._vertical_split(
            self.pc_left_body, pf, pit, min_top=92, min_bottom=120, ratio=0.42)
        # v40：移除已选 / 清空清单 只放在传输工作台，播控页不再添加。

        # ===== 右栏：标题行折叠按钮在最左；搜索行在标题下一行、列表上方 =====
        rb = self.pc_right_title.master
        lf = self.lib_tree.master
        rb.destroy()
        lib_title = str(lf.cget("text")).strip() or "机器内歌曲（拖到中间加入播放队列）"
        lf.configure(text="")
        hdr2 = ttk.Frame(lf)
        lf.configure(labelwidget=hdr2)
        self.pc_right_fold_btn = ttk.Button(
            hdr2, text="»", width=3, command=self.action_pc_fold_right)
        self.pc_right_fold_btn.pack(side="left", padx=(0, 4))
        ttk.Label(hdr2, text=" " + lib_title + " ",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")

        # 搜索行重建为 lf 的第二行（原控件是兄弟节点，tk 不允许换父容器）。
        old_sb = self.search_bar
        old_sb.destroy()
        sb = ttk.Frame(lf, padding=(0, 0, 0, 2))
        sb.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(sb, text="搜索:").pack(side="left", padx=(0, 3))
        ttk.Entry(sb, textvariable=self.lib_search_var,
                  width=14).pack(side="left", padx=(0, 3))
        ttk.Combobox(sb, textvariable=self.lib_search_field_var,
                     values=("标题", "艺人", "专辑", "ID", "文件名"),
                     state="readonly", width=7).pack(side="left", padx=(0, 3))
        ttk.Button(sb, text="搜索", command=self.action_find_lib).pack(
            side="left", padx=(0, 3))
        ttk.Checkbutton(sb, text="只显示匹配", variable=self.lib_search_only_var,
                        command=self._render_lib).pack(side="left")
        self.search_bar = sb
        sb.bind_all("<F3>", lambda _e: self.action_find_lib())

        # 列表与滚动条整体下移一行，为搜索行让位（题头已由 labelwidget 承担）。
        tree = self.lib_tree
        scrolls = [w for w in lf.winfo_children() if isinstance(w, ttk.Scrollbar)]
        for w in scrolls + [tree]:
            info = dict(w.grid_info())
            w.grid(row=int(info.get("row", 0)) + 1,
                   column=int(info.get("column", 0)),
                   columnspan=int(info.get("columnspan", 1)),
                   rowspan=int(info.get("rowspan", 1)),
                   sticky=info.get("sticky", ""),
                   padx=info.get("padx", 0), pady=info.get("pady", 0))
        lf.rowconfigure(0, weight=0)
        lf.rowconfigure(1, weight=1)

        # 取消右侧机器内歌曲“双击修改元数据”功能（只针对主套 lib_tree）
        tree.unbind("<Double-1>")

        # 删除右侧底部按钮行与暂存同步行，只保留曲库框体
        for w in list(self.pc_right_body.winfo_children()):
            if w is lf:
                continue
            txt = " ".join(str(c.cget("text")) for c in w.winfo_children()
                           if "text" in c.keys())
            if ("刷新曲库" in txt or "下载选中" in txt or "改元数据" in txt
                    or "同步暂存修改" in txt):
                w.destroy()
        # 保留 dirty_var / sync_btn 对象供传输工作台副套复用（不显示）
        if not hasattr(self, "dirty_var"):
            self.dirty_var = tk.StringVar(value="无暂存修改")

        self._rebuild_stage_ui()

    def _play_info_row(self):
        """定位“播放位置 / 总时长 / 定位”那一行容器。"""
        target = str(self.play_pos_label_var)

        def walk(w):
            for c in w.winfo_children():
                try:
                    if "textvariable" in c.keys() and str(c.cget("textvariable")) == target:
                        return c.master
                except Exception:
                    pass
                found = walk(c)
                if found is not None:
                    return found
            return None
        return walk(self.pc_mode_frame)

    def _setup_play_scale_tooltip(self):
        """拖动进度条时在游标上方显示小时间窗，松手消失。"""
        scale = getattr(self, "play_scale", None)
        if scale is None:
            return
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.withdraw()
        lab = ttk.Label(tip, text="00:00", font=("Consolas", 8),
                        padding=(3, 1), background="#FFF3B0", foreground="#000")
        lab.pack()
        self._play_tip, self._play_tip_lbl = tip, lab

        def show(e):
            try:
                width = max(1, scale.winfo_width())
                x = max(0, min(int(e.x), width))
                to = float(scale.cget("to") or 300)
                sec = int(to * x / width)
                lab.configure(text=self._fmt_mmss(sec))
                px = scale.winfo_rootx() + x - lab.winfo_reqwidth() // 2
                py = scale.winfo_rooty() - lab.winfo_reqheight() - 4
                tip.geometry(f"+{px}+{py}")
                tip.deiconify()
            except Exception:
                pass

        def hide(_e=None):
            try:
                tip.withdraw()
            except Exception:
                pass

        for seq in ("<ButtonPress-1>", "<B1-Motion>"):
            scale.bind(seq, show, add="+")
        for seq in ("<ButtonRelease-1>", "<Leave>"):
            scale.bind(seq, hide, add="+")

    def _stage_row_with(self, text):
        for w in self.pc_mode_body.winfo_children():
            if isinstance(w, ttk.Frame) and any(
                    "text" in c.keys() and text in str(c.cget("text"))
                    for c in w.winfo_children()):
                return w
        return None

    def _rebuild_stage_ui(self):
        """v35：精简约播控模式的按钮行、列表列，并加入音量/EAX。"""
        body = self.pc_mode_body

        # 1) 整行删除：读取清单 / 载入机器清单 / 加入选中曲目 / 机器清单下拉框
        qrow1 = self._stage_row_with("读取清单")
        if qrow1 is not None:
            qrow1.destroy()
        self.stage_pl_combo = None
        self.stage_pl_var = tk.StringVar(value="")

        qrow2 = self._stage_row_with("GO")
        qrow3 = self._stage_row_with("播放模式")
        if qrow2 is None or qrow3 is None:
            return

        # 2) 删除“写回队列 / 重刷队列”，并摘出要移到下一行的三个控件
        clear_cmd = keymap_cmd = None
        stage_var = getattr(self, "stage_mode_var", None)
        for w in list(qrow2.winfo_children()):
            txt = str(w.cget("text")) if "text" in w.keys() else ""
            if isinstance(w, ttk.Checkbutton) and "舞台按键" in txt:
                w.destroy()
            elif "清空队列" in txt:
                w.destroy()
            elif "按键映射" in txt:
                w.destroy()
            elif "写回队列" in txt or "重刷队列" in txt:
                w.destroy()

        # 3) v36：上一曲 / 下一曲之间插入“软件播放”（按软件清单选中曲目播放）
        next_btn = next((w for w in qrow2.winfo_children()
                         if "text" in w.keys() and "下一曲" in str(w.cget("text"))), None)
        # v41：播控页的软件播放与 ★GO 一样，播放当前选定后把选定跳到下一首；
        # 机器内歌曲右键的“播放选中曲目”仍保持只播放、不跳选。
        play_soft = ttk.Button(
            qrow2, text="▶ 软件播放",
            command=lambda: self.action_play_selected(advance_selection=True),
            width=9, state="disabled")  # v37：未连接时不可用
        if next_btn is not None:
            play_soft.pack(side="left", padx=(2, 2), before=next_btn)
        else:
            play_soft.pack(side="left", padx=(2, 2))
        self.play_soft_btn = play_soft

        # 4) 音量 + EAX 移到“播放位置 / 总时长 / 定位”这一行（停止按钮原位置留空）
        prow = self._play_info_row()
        vol_box = ttk.Frame(prow) if prow is not None else ttk.Frame(qrow2)
        vol_box.pack(side="left", padx=(10, 2))
        ttk.Label(vol_box, text="音量:").pack(side="left", padx=(0, 2))
        vol2 = ttk.Scale(vol_box, from_=0, to=100, orient="horizontal", length=128,
                         variable=self.vol_var, command=self.on_volume_change)
        vol2.pack(side="left")
        vol2.bind("<ButtonRelease-1>", lambda e: self.action_set_volume())
        vol2.configure(state="disabled")
        vol_lbl2 = ttk.Label(vol_box, text="50", width=3, foreground=self.pal["ok"])
        vol_lbl2.pack(side="left", padx=(2, 0))
        eax2 = ttk.Checkbutton(vol_box, text="EAX", variable=self.eax_on_var,
                               command=self.action_eax_toggle, state="disabled")
        eax2.pack(side="left", padx=(4, 2))
        self.vol2_scale, self.vol2_lbl, self.eax2_chk = vol2, vol_lbl2, eax2
        self._sync_volume_labels()
        self.vol_var.trace_add("write", lambda *_: self._sync_volume_labels())

        # 4) 把“清空队列 / 舞台按键 / 按键映射”追加到“播放模式”行之后
        if stage_var is None:
            stage_var = getattr(self, "stage_mode_var", None)
        clear_cmd = self.action_stage_clear
        keymap_cmd = self.action_stage_keymap
        ttk.Button(qrow3, text="清空队列", command=clear_cmd, width=9).pack(
            side="left", padx=(8, 2))
        ttk.Checkbutton(qrow3, text="舞台按键", variable=stage_var, width=9,
                        command=self._sync_stage_mode).pack(side="left", padx=(2, 2))
        ttk.Button(qrow3, text="按键映射…", command=keymap_cmd, width=9).pack(
            side="left", padx=(2, 2))

        # 5) 两行按钮统一到刚好容纳“暂停/继续”的宽度
        for row in (qrow2, qrow3):
            for w in row.winfo_children():
                if isinstance(w, (ttk.Button, ttk.Checkbutton)):
                    w.configure(width=9)

        # 6) 播控清单：# 为锁定序号；用“状态”列替换“机器序号”列
        tree = self.stage_tree
        tree.configure(columns=("num", "state", "title", "artist", "length"))
        for col, head, width in (("num", "#", 34), ("state", "状态", 46),
                                 ("title", "标题", 210), ("artist", "艺人", 120),
                                 ("length", "时长", 58)):
            tree.heading(col, text=head)
            tree.column(col, width=width,
                        anchor="center" if col in ("num", "state") else "w")
        self._patch_stage_state()
        self._tick_stage_state()
        self._setup_play_scale_tooltip()

    def _sync_volume_labels(self):
        try:
            v = str(int(self.vol_var.get()))
        except Exception:
            return
        for name in ("vol_lbl", "vol2_lbl"):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    w.configure(text=v)
                except Exception:
                    pass

    def on_volume_change(self, value):
        super().on_volume_change(value)
        self._sync_volume_labels()

    def _sync_conn_buttons(self):
        super()._sync_conn_buttons()
        connected = bool(getattr(self, "connected", False))
        # v40：播放状态同步每 2 秒会短占 worker；若按钮状态依赖 busy，
        # 顶栏“断开设备”与音量/EAX 就会周期性闪。这里只跟随连接状态，
        # 仅真正传歌期间禁止断开，避免传输中误拔设备。
        busy_transfer = bool(getattr(self, "_transfer_active", False))
        for name, state in (
                ("disconnect_btn", "normal" if connected and not busy_transfer else "disabled"),
                ("connect_btn", "normal" if not connected and not busy_transfer else "disabled")):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    w.configure(state=state)
                except Exception:
                    pass
        vol_state = "normal" if connected else "disabled"
        for name in ("vol2_scale", "eax2_chk"):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    w.configure(state=vol_state)
                except Exception:
                    pass
        # v39：软件播放按钮只在【已连接 且 播控模式已开启】时可用。
        # 绝不依赖 worker_busy——播放同步每 2 秒触发一次 busy，依赖它会让按钮闪烁。
        soft = getattr(self, "play_soft_btn", None)
        if soft is not None:
            try:
                can = (getattr(self, "connected", False)
                       and getattr(self, "_pcmode_enabled", False))
                soft.configure(state="normal" if can else "disabled")
            except Exception:
                pass

    def action_play_selected(self, advance_selection=False):
        """软件播放当前选中曲目；播控页按钮额外把选定跳到下一首（同 GO 规则）。"""
        t, _source = self._selected_track_anywhere()
        if not t:
            messagebox.showinfo("提示", "请先在【机器内歌曲 / 清单内歌曲 / 播控清单】选中一首曲目。")
            return
        tid = t.get("trackid")
        exist, idx = self._stage_find_by_tid(tid)
        if exist is None:
            self.stage_tracks.append(self._new_stage_item(t))
            idx = len(self.stage_tracks) - 1
        self.stage_index = idx
        self.action_stage_go(idx, advance_selection=advance_selection)

    def action_stage_go(self, index=None, manual=True, advance_selection=None):
        """v40：所有模式都由软件逐曲控制；点 ★GO 播放当前选中后选定跳下一首，
        “软件播放”只播放当前选中不跳。播放不重排清单，# 始终 1..N。"""
        if not self.stage_tracks:
            messagebox.showinfo("提示", "请先载入播放清单或加入曲目。")
            return
        go_click = index is None            # ★GO 按钮（无参数）与软件播放/上下曲的区别
        if index is None:
            index = self.stage_index if self.stage_index is not None else 0
        if advance_selection is None:
            advance_selection = go_click
        n = len(self.stage_tracks)
        index = max(0, min(int(index), n - 1))
        t = self.stage_tracks[index]
        tid = t.get("trackid")
        key = self._cur_mode_key() if hasattr(self, "_cur_mode_key") else "go"
        shuffle_mode = key in ("shuffle", "shuffle_repeat")

        # 人为操控（GO/上下曲/软件播放）清空随机“已播”标记；自动续播保留。
        if manual:
            self._played_marks = set()
        if not hasattr(self, "_played_marks"):
            self._played_marks = set()

        # ★计时基准：每首新曲从 0 起算。seek 后也由此处把旧进度清掉，
        # 否则下一首会带着上一首的定位秒数计时，播一半就被软件切走。
        self.play_elapsed_sec = 0.0
        self.play_pos_var.set(0.0)
        self.play_pos_label_var.set("00:00")
        self.seek_var.set("0")
        self.play_seek_target = 0
        self.play_seek_at = 0.0
        # v43：设备时钟倍率跨曲稳定，保留上一首学到的 _play_rate，
        # 只重置采样基准。旧版每首归 1.0 导致新曲前几秒计时偏慢、曲末滞后。
        if getattr(self, "_play_rate", None) is None:
            self._play_rate = 1.0
        self._rate_track = None
        self._rate_last_elapsed = None
        self._rate_last_t = None
        self._end_dispatched = False       # v43：本曲曲终提前量是否已发指令
        self.play_state = "playing"
        self.playstat_var.set("状态：播放中（软件控制）")
        self._duration_synced_tid = None

        self._play_stage_index = index
        self.play_queue_pos = index
        self.play_track_info = t
        self.play_track_id = tid
        self._sync_duration_from_track(track=t, reason="软件逐曲播放")
        mode_hint = {"shuffle": "随机", "shuffle_repeat": "随机重复",
                     "single": "单曲", "repeat_one": "单曲重复",
                     "normal": "顺序", "repeat_all": "循环"}.get(key, "GO")
        self.play_track_var.set(
            f"当前曲目：{t.get('title', '未命名')}"
            + (f" — {t.get('artist')}" if t.get("artist") else "")
            + f"   [{index + 1}/{n}]"
            + (f"   [已播 {len(self._played_marks)}/{n}]" if shuffle_mode else ""))
        self._play_history.append(tid)
        if len(self._play_history) > 100:
            self._play_history = self._play_history[-100:]

        if manual:
            self._auto_advance_pos = 0
            self._shuffle_order = None
        # v40：设备队列只放当前一首，后续每首由软件在曲终显式 play_track。
        self._device_order = [index]
        # ★GO（任意模式）播放当前选中后，选定自动指向下一行；
        # 软件播放 / 上一曲 / 下一曲保持选定点不变。
        self.stage_index = (index + 1) % n if advance_selection else index
        self._auto_loaded = False
        self._obs_last = None
        self._was_playing = True
        self._boundary_handled = False
        self.play_last_sync = time.monotonic()
        self._render_stage()

        def fn(d):
            # v43：先清空机器播放队列，杜绝残留旧曲让设备自行切歌——这是
            # “机器已经播下一曲、软件才发指令”的根因；队列只保留当前一首。
            try:
                d.clear_play_queue()
            except Exception:
                pass
            ok = d.play_track(tid)
            self.q.put(("play_started", tid, ok))
            self.q.put(("log", f"[{'OK' if ok else '!'}] 播放【{t.get('title', '?')}】"
                               f"（ID {tid}），{mode_hint}逐曲，曲终由软件调度。"))
        self._worker(fn, label=f"正在播放【{t.get('title', '?')}】 ...", quiet=True)

    def _remap_play_sync(self, ev):
        """设备队列位置 → 界面清单位置，保证播放不重排界面。"""
        order = getattr(self, "_device_order", None) or []
        if not order:
            return
        obs = ev[1] if len(ev) > 1 else {}
        adv = ev[2] if len(ev) > 2 and isinstance(ev[2], dict) else {}
        pos = obs.get("queue_pos")
        if not isinstance(pos, int) or not (0 <= pos < len(order)):
            return
        ui = order[pos]
        if not (0 <= ui < len(self.stage_tracks)):
            return
        self.play_queue_pos = ui
        self._play_stage_index = ui
        if adv.get("advanced"):
            self.stage_index = (ui + 1) % len(self.stage_tracks)
        t = self.stage_tracks[ui]
        self.play_track_info = t
        self.play_track_id = t.get("trackid")
        self._sync_duration_from_track(track=t, reason="设备位置同步")
        self._render_stage()
        self._update_play_track_label()

    def action_stage_step(self, delta: int):
        """v38：上一曲/下一曲以“当前正在播放的曲目”为基准（所有播放模式一致），
        不再以目标/底图选中行为基准。点击属于人为操控，会清空随机已播标记。"""
        if not self.stage_tracks:
            return
        n = len(self.stage_tracks)
        cur = getattr(self, "_play_stage_index", None)
        if not isinstance(cur, int) or not (0 <= cur < n):
            cur = getattr(self, "play_queue_pos", None)
        if not isinstance(cur, int) or not (0 <= cur < n):
            cur = self.stage_index if isinstance(self.stage_index, int) else 0
        cur = max(0, min(int(cur), n - 1))
        idx = (cur + delta) % n
        self.action_stage_go(idx, manual=True)
        self.stage_status_var.set(
            f"{'上一曲' if delta < 0 else '下一曲'}：从当前播放曲目切到第 {idx + 1} 首")

    def _software_stop(self, reason):
        """软件判定曲终后主动给机器发停止指令并归零 UI。"""
        self.play_state = "stopped"
        self.playstat_var.set("状态：" + self._play_state_label("stopped") + f"（{reason}）")
        self.stage_status_var.set(f"{reason}（已停止）")
        self._reset_play_progress()
        self._auto_loaded = False
        self._device_order = []
        self._boundary_handled = True

        def _stop_fn(d):
            try:
                ok = d.play_control(base.pde.NJB3Device.PLAY_STOP)
                self.q.put(("log", f"[{reason}] 已向机器发送停止指令"
                                   + ("。" if ok else "（设备未确认）。")))
            except Exception as e:
                self.q.put(("log", f"[{reason}] 停止指令发送失败：{e}"))
        self._worker(_stop_fn, quiet=True)

    def _auto_advance_on_end(self):
        """v40：软件逐曲调度（模式键 go/single/repeat_one/normal/repeat_all/shuffle/shuffle_repeat）。

        · GO：曲终发停止指令；单曲：不发停止指令，只把 UI 归位；
        · 单曲重复：重新播放正在播的这首；
        · 顺序：播放选定（下一首），播完最后一条才停止；
        · 循环：播放选定（下一首），到底回到队首，往复；
        · 随机：播放选定，给刚播完的曲打标，随后选定随机跳到未播曲；全播完停止；
        · 随机重复：同上，但打完最后一首的标记后清标，随机跳非当前曲重来。
        """
        # 重入由 _on_queue_end 的边界标志与 action_stage_go 重置的播放状态负责：
        # 这里不可无条件返回，否则模式切换后的首次曲终会被旧标志吞掉。
        key = self._cur_mode_key() if hasattr(self, "_cur_mode_key") else "go"
        n = len(getattr(self, "stage_tracks", []) or [])
        if not n:
            return
        if key == "go":
            self._software_stop("GO 单曲播放完毕")
            return
        if key == "single":
            # 没有为单曲模式增加停止控制指令：这里只把软件状态归位，不碰设备。
            self.play_state = "stopped"
            self.playstat_var.set("状态：播放结束（单曲，未发停止指令）")
            self.stage_status_var.set("单曲播放完毕（软件不发停止指令）")
            self._reset_play_progress()
            self._boundary_handled = True
            return
        cur = getattr(self, "_play_stage_index", None)
        if not isinstance(cur, int) or not (0 <= cur < n):
            cur = 0
        if key == "repeat_one":
            self.stage_status_var.set("单曲重复：重播当前曲目")
            self.action_stage_go(cur, manual=False)
            return

        sel = getattr(self, "stage_index", None)
        if not isinstance(sel, int) or not (0 <= sel < n):
            sel = (cur + 1) % n
        if key == "normal" and cur == n - 1:
            self._software_stop("顺序播放完毕")
            return

        marks = getattr(self, "_played_marks", None)
        if marks is None:
            marks = self._played_marks = set()
        if key in ("shuffle", "shuffle_repeat"):
            import random as _random
            marks.add(cur)                      # 给刚播完的曲打“已播”标签
            pool = [i for i in range(n) if i not in marks]
            if not pool:
                if key == "shuffle":
                    self._software_stop("随机播放完毕")
                    return
                marks.clear()                   # 随机重复：一轮结束清标
                pool = [i for i in range(n) if i != cur] or [cur]
            nxt = _random.choice(pool)
            rest = [i for i in range(n) if i not in marks and i != nxt]
            if not rest and key == "shuffle_repeat":
                marks.clear()
                rest = [i for i in range(n) if i != nxt] or [nxt]
            nxt_sel = _random.choice(rest) if rest else nxt
            self.stage_status_var.set(
                f"随机{'重复' if key == 'shuffle_repeat' else '播放'}："
                f"已播 {len(marks)}/{n}，播放第 {nxt + 1} 首")
        else:
            nxt = sel % n                       # 设备按软件选定播放
            nxt_sel = (nxt + 1) % n             # 播完后选定跳下一首；循环模式自然回卷

        self.action_stage_go(nxt, manual=False)
        # action_stage_go(manual=False) 保持选定点，这里按模式覆盖为“下一目标”。
        self.stage_index = nxt_sel
        self._render_stage()

    def _on_queue_end(self):
        """v40：所有模式都逐曲由软件控制，设备队尾边界统一交给曲终调度。"""
        if getattr(self, "_boundary_handled", False):
            return
        self._boundary_handled = True
        self._auto_advance_on_end()

    def _new_stage_item(self, t):
        """锁定序号：按当前清单长度分配 1..N，拖动排序时不再变化。"""
        item = dict(t)
        self.stage_seq_counter += 1
        item["_seq"] = len(getattr(self, "stage_tracks", []) or []) + 1
        item["_mseq"] = None
        return item

    def _render_stage(self):
        # v36：# 永远是位置序号 1..N，播放/拖动都不改变曲目在这张表里的位置。
        for i, t in enumerate(getattr(self, "stage_tracks", []) or []):
            t["_seq"] = i + 1
        super()._render_stage()
        self._patch_stage_state()

    def _patch_stage_state(self):
        tree = getattr(self, "stage_tree", None)
        if tree is None or "state" not in tree["columns"]:
            return
        state = getattr(self, "play_state", "unknown")
        symbol = "▶" if state == "playing" else "⏸" if state == "paused" else ""
        pos = getattr(self, "_play_stage_index", None)
        if not isinstance(pos, int) or not (0 <= pos < len(self.stage_tracks)):
            pos = getattr(self, "play_queue_pos", None)
        if not isinstance(pos, int) or not (0 <= pos < len(self.stage_tracks)):
            pos = getattr(self, "stage_index", None)
        for i, iid in enumerate(tree.get_children()):
            vals = list(tree.item(iid, "values"))
            if len(vals) >= 5:
                vals[1] = symbol if (symbol and i == pos) else ""
                tree.item(iid, values=vals)

    def _tick_stage_state(self):
        try:
            self._patch_stage_state()
        except Exception:
            pass
        self.root.after(800, self._tick_stage_state)

    # 以下功能在 v35 中已从界面移除，保留空实现兼容遗留事件引用。
    def action_stage_read_queue(self, quiet=False):
        return False

    def action_stage_load_playlist(self):
        return False

    def action_stage_add_selected(self):
        return False

    def action_stage_save_queue(self, rebuild=False):
        return False

    def action_stage_rebuild_queue(self):
        return False

    def on_stage_combo_select(self, event=None):
        return None

    def _refresh_stage_playlists(self):
        return None

    # -------------------------------------------------------------- 界面
    def _build_ui(self):
        r = self.root
        r.title("NJB3 Studio")
        r.geometry("1500x900")
        r.minsize(980, 640)

        p = self.pal
        self._pc_handles = {}
        self._wb_handles = {}
        self.workbench_collapsed = {"explorer": False, "transfer": False}

        # ---------- 顶栏（全部靠左，按功能顺序排列） ----------
        top = tk.Frame(r, bg=p["chrome"], padx=10, pady=8)
        top.pack(fill="x")
        self._chrome_frames = [top]
        self._chrome_labels = []
        title = tk.Label(top, text="NJB3 Studio", bg=p["chrome"], fg=p["text"],
                         font=("Microsoft YaHei UI", 14, "bold"))
        title.pack(side="left", padx=(0, 8))
        self._chrome_labels.append((title, "text"))

        ttk.Button(top, text="🎨 外观", command=self.action_skin_settings).pack(side="left", padx=2)
        ttk.Button(top, text="复位布局", command=self._ask_reset_layout).pack(side="left", padx=2)

        self.main_view_buttons = {}
        for pid, label in self.MAIN_VIEW_BUTTONS:
            btn = ttk.Button(top, text=label,
                             command=lambda p_id=pid: self.action_show_main(p_id))
            btn.pack(side="left", padx=2)
            self.main_view_buttons[pid] = btn

        self.disconnect_btn = ttk.Button(top, text="⏏ 断开设备", command=self.action_disconnect,
                                         state="disabled")
        self.disconnect_btn.pack(side="left", padx=(8, 2))
        self.connect_btn = ttk.Button(top, text="连接设备", command=self.action_connect)
        self.connect_btn.pack(side="left", padx=2)

        self.status_var = tk.StringVar(value="未连接 - 请确认设备并以管理员身份运行驱动安装")
        self.status_lbl = tk.Label(top, textvariable=self.status_var, bg=p["chrome"],
                                   fg="#e06a5e", font=("Microsoft YaHei UI", 9),
                                   wraplength=360, anchor="w", justify="left")
        self.status_lbl.pack(side="left", padx=(8, 0))

        # ---------- 整页工作台 ----------
        stage = tk.Frame(r, bg=p["bg"])
        stage.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        self.stage = stage

        self.dock = dockmod.DockManager(
            stage, p, config_path=str(DOCK_CFG),
            on_relayout=self._after_dock_relayout,
            default_layout=self.studio_default_layout,
            show_header=False)          # 整页无“眉头”
        # 先建播控工作台（建立主套树控件），再建传输工作台与控制工作台。
        self.dock.add_panel("playctrl", "播控工作台", self._build_playctrl_page)
        self.dock.add_panel("machine", "传输工作台", self._build_machine_page)
        self.dock.add_panel("other", "控制工作台", self._build_other_page)

        self._tab_map = {pid: pid for pid in MAIN_IDS}
        self.nb = _NBShim(self)
        self.tab_playctrl = "playctrl"
        self.tab_machine = "machine"
        self.tab_transfer = "machine"   # 传输队列已并入传输工作台
        self.tab_other = "other"

        ids = list(MAIN_IDS)
        self.dock.load_or_default(ids, defaults=self.studio_default_layout(ids))
        # v45：即使旧配置记住了别的页签，每次启动也强制回到控制工作台。
        self.action_show_main("other")

        # v41：内部边栏分隔条统一收窄为 4px（与自建 sash 一致），避免黑条。
        for attr in ("explorer_paned", "machine_paned", "transfer_paned"):
            pane = getattr(self, attr, None)
            if pane is not None:
                try:
                    pane.configure(sashwidth=4)
                except Exception:
                    pass

        self._sync_main_view_buttons()
        self._configure_tree_style_map()
        self._apply_current_skin()
        self.apply_studio_theme(self.theme_name, save=False)
        try:
            self._render_queue()
        except Exception:
            pass

        for tree in (getattr(self, "pl_tree", None), getattr(self, "pl_tree2", None)):
            if tree is not None:
                tree.bind("<FocusOut>", lambda e, t=tree: self._pl_focus_out(t), add="+")
        # v0.1.0：底部状态栏左侧提示 + 右侧版本号
        status_bar = ttk.Frame(r)
        hint = ttk.Label(status_bar, text="提示：顶部按钮切换整页 · 控制工作台内小框体可拖动/贴边/调大小 · 分隔条可拖动",
                         style="Status.TLabel", anchor="w", padding=(10, 3))
        hint.pack(side="left", fill="x", expand=True)
        ver_lbl = ttk.Label(status_bar, text=f"版本：{APP_VERSION} {APP_BUILD_TAG}",
                            style="Status.TLabel", anchor="e", padding=(10, 3))
        ver_lbl.pack(side="right")
        status_bar.pack(fill="x", side="bottom")
        self.root.after(80, lambda: (self._push_workbench_sash(), self._pc_push_sash()))

    def _is_old_control_layout(self, node):
        """判断是否为 v36 及以前的“日志在下方”控制页布局。
        v37 新布局根节点一定是 horizontal（左模块 + 右日志）；
        旧布局根节点是 vertical，因此据此迁移。"""
        if not isinstance(node, dict):
            return False
        return node.get("type") == "paned" and node.get("orient") == "vertical"

    def _after_dock_relayout(self):
        try:
            self._apply_focus_highlight()
        except Exception:
            pass
        self._sync_main_view_buttons()

    # -------------------------------------------------------------- 播控工作台
    def _build_playctrl_page(self, parent):
        self._build_playctrl_tab(parent)
        # 原播放控制框体只隐藏不销毁：播放控制已提升到控制工作台。
        orig = getattr(self, "ctrl_frame", None)
        if orig is not None:
            self._playctrl_orig_frame = orig
            try:
                orig.pack_forget()
            except Exception:
                pass

    # -------------------------------------------------------------- 传输工作台
    def _build_machine_page(self, parent):
        """资源管理器（左）+ 机器曲库（中）+ 传输队列（右）。"""
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self.workbench_paned = paned
        try:
            paned.configure(sashwidth=8)
        except Exception:
            pass

        ex = ttk.Frame(paned); paned.add(ex, weight=2)
        mc = ttk.Frame(paned); paned.add(mc, weight=4)
        tr = ttk.Frame(paned); paned.add(tr, weight=3)
        self.wb_ex, self.wb_tr = ex, tr

        self._build_explorer_panel(ex)
        self._build_machine_tab(mc)
        self._build_transfer_tab(tr)

        # v38：左右侧折叠后 sash 钉死为 14px，不能拖动放大。
        self._pin_folded_sash(
            paned, 0, 14,
            lambda: bool(getattr(self, "workbench_collapsed", {}).get("explorer")))
        self._pin_folded_sash(
            paned, 1, lambda: max(14, paned.winfo_width() - 14),
            lambda: bool(getattr(self, "workbench_collapsed", {}).get("transfer")))

    # -------------------------------------------------------------- 控制工作台
    def _build_other_page(self, parent):
        """内部小框体由第二个 DockManager 承载，可拖动、贴边、调大小。"""
        cd = dockmod.DockManager(parent, self.pal,
                                 config_path=str(CONTROL_DOCK_CFG),
                                 show_header=True, slim=True,
                                 show_panel_buttons=False,
                                 allow_panel_menu=False)
        self.control_dock = cd
        cd.default_layout = control_default_tree   # v37：复位布局也用新的默认分布
        builders = (
            ("devstat", "设备状态", self._build_devstat_module),
            ("playback", "播放控制", self._build_playback_module),
            ("driver", "驱动安装", self._build_driver_module),
            ("refresh", "设备信息与数据刷新", self._build_refresh_module),
            ("appearance", "界面外观 / Skin", self._build_appearance_module),
            ("log", "日志", self._build_log_module),
        )
        for pid, title, builder in builders:
            cd.add_panel(pid, title, builder)
        ids = [pid for pid, _t, _b in builders]
        cd.load_or_default(ids, defaults=control_default_tree(ids))
        # v37：旧配置是“日志在下方、模块在上方”的上下结构，自动迁移到
        # 新的“左模块 + 右日志”分布，避免沿用会被挤没的旧嵌套布局。
        if self._is_old_control_layout(cd.tree):
            cd.tree = control_default_tree(ids)
            cd.save()
            cd.relayout()
        cd.apply_content_mins()
        # v37：三个易被挤没的框体抬高最小高度，拖到下限即停，内容始终可见。
        cd.set_panel_min_heights({"devstat": 88, "playback": 108, "driver": 118,
                                  "refresh": 118, "appearance": 118, "log": 96})

    def _build_transfer_tab(self, parent):
        """传输队列：表格在上，操作区在下；第一行操作按钮，第二行查看 + 传输。"""
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        self.transfer_head = head
        self.transfer_fold_btn = ttk.Button(head, text="»", width=3,
                                            command=self.action_transfer_fold)
        self.transfer_fold_btn.pack(side="left", padx=(0, 4))
        ttk.Label(head, text="传输队列 / 元数据 / 操作",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.transfer_collapsed = False

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        self.transfer_body = body

        paned = tk.PanedWindow(body, orient=tk.VERTICAL, sashwidth=4,
                               bg=self.pal.get("sash", self.pal.get("border", "#555")))
        paned.pack(fill="both", expand=True)
        self.transfer_paned = paned

        queue_area = ttk.Frame(paned)
        ops_area = ttk.Frame(paned)
        paned.add(queue_area)
        paned.add(ops_area)
        # 拖到最小时仍需看全“查看: … 传输: …”整行。
        paned.paneconfigure(queue_area, stretch="always", minsize=190)
        paned.paneconfigure(ops_area, stretch="always", minsize=132)

        tf = ttk.LabelFrame(queue_area, text=" 传输队列（点表头排序，可边传边增删减/改元数据） ",
                            padding=(6, 5))
        tf.pack(fill="both", expand=True)

        cols = ("#", "status", "title", "artist", "album", "folder",
                "target_pl", "size", "progress")
        heads = ("#", "状态", "标题", "艺人", "专辑", "分类文件夹", "目标清单", "大小", "进度")
        widths = (34, 62, 210, 110, 130, 130, 106, 78, 110)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended",
                                 height=12)
        for col, head_text, width in zip(cols, heads, widths):
            self.tree.heading(col, text=head_text,
                              command=lambda cc=col: self.action_sort_queue(cc))
            self.tree.column(col, width=width, anchor="w")
        self.tree.column("#", width=34, anchor="center")
        self.tree.column("status", width=62, anchor="center")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        # v36：队列底部横向滚动条，同时横向推动下方元数据输入区。
        meta_shift = {"x": 0}

        def shift_meta(lo):
            def apply():
                try:
                    overflow = max(0, self._meta_inner.winfo_reqwidth()
                                   - self._meta_view.winfo_width())
                    x = -int(float(lo) * overflow)
                    meta_shift["x"] = x
                    self._meta_view.coords(self._meta_window, x, 0)
                except Exception:
                    pass
            self.root.after_idle(apply)

        hsb = ttk.Scrollbar(tf, orient="horizontal")
        def on_xscroll(*args):
            if args:
                self.tree.xview(*args)
            shift_meta(self.tree.xview()[0])
        hsb.configure(command=on_xscroll)
        self.tree.configure(xscrollcommand=lambda lo, hi: (hsb.set(lo, hi), shift_meta(lo)))
        self.queue_hscroll = hsb
        self._meta_shift = meta_shift
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<ButtonPress-1>", self.on_queue_drag_start)
        self.tree.bind("<B1-Motion>", self.on_queue_drag_motion)

        ed = ttk.LabelFrame(queue_area,
                            text=" 选中项元数据（双击列表行载入，改完点“应用到选中”） ",
                            padding=(6, 4))
        ed.pack(side="bottom", fill="x", pady=(6, 0))
        self.ed = {}
        self._meta_view = tk.Canvas(ed, height=34, highlightthickness=0,
                                    bg=self.pal.get("panel", "#ffffff"))
        self._meta_view.pack(fill="x")
        self._meta_inner = ttk.Frame(self._meta_view)
        self._meta_window = self._meta_view.create_window(
            (0, 0), window=self._meta_inner, anchor="nw")
        fields = [("title", "标题", 18), ("artist", "艺人", 12), ("album", "专辑", 14),
                  ("genre", "流派", 10), ("year", "年份", 6), ("folder", "分类文件夹", 14)]
        row1 = ttk.Frame(self._meta_inner)
        row1.pack(fill="x")
        for key, lab, width in fields:
            ttk.Label(row1, text=lab + ":").pack(side="left", padx=(5, 2))
            var = tk.StringVar()
            ttk.Entry(row1, textvariable=var, width=width).pack(side="left", padx=(0, 5))
            self.ed[key] = var
        ttk.Button(row1, text="应用到选中", command=self.action_apply_edit).pack(
            side="right", padx=4)

        ops = ttk.LabelFrame(ops_area, text=" 操作 / 查看 / 传输 ", padding=(6, 5))
        ops.pack(fill="both", expand=True)

        op_row = ttk.Frame(ops)
        op_row.pack(fill="x")
        for txt, cmd in (("＋ 添加 MP3", self.action_add_files),
                         ("移除选中", self.action_remove_sel),
                         ("清空列表", self.action_clear),
                         ("↑ 上移", lambda: self.action_move(-1)),
                         ("↓ 下移", lambda: self.action_move(1)),
                         ("导入 m3u", self.action_import_m3u)):
            ttk.Button(op_row, text=txt, command=cmd).pack(side="left", padx=(0, 4), pady=2)

        view_row = ttk.Frame(ops)
        view_row.pack(fill="x", pady=(4, 0))
        ttk.Label(view_row, text="查看:").pack(side="left", padx=(0, 4))
        self.view_combo = ttk.Combobox(view_row, values=("详细信息", "紧凑"),
                                       state="readonly", width=12)
        self.view_combo.set("详细信息")
        self.view_combo.bind("<<ComboboxSelected>>", self.on_view_change)
        self.view_combo.pack(side="left", padx=(0, 12))
        ttk.Label(view_row, text="传输:").pack(side="left", padx=(0, 4))
        self.start_btn = ttk.Button(view_row, text="▶ 开始传输",
                                    command=self.action_start_transfer)
        self.start_btn.pack(side="left", padx=(0, 4))
        # v38：该按钮已移到传输进度弹窗内；此处保留隐藏对象以兼容测试与状态同步。
        self.stop_btn = ttk.Button(view_row, text="传完本首后停止  ■",
                                   command=self.action_stop, state="disabled")
        self.stop_btn.pack_forget()

    # ---- 传输队列：目标清单列 -------------------------------------------
    def _refresh_row(self, idx: int):
        iid = str(idx)
        t = self.queue_rows[idx]
        tag = t["tags"]
        try:
            self.tree.item(iid, values=(
                idx + 1, t["status"], tag.get("title", ""), tag.get("artist", ""),
                tag.get("album", ""), tag.get("folder", ""),
                t.get("target_pl_name", ""),
                f'{tag.get("size", 0) // 1024}KB', f'{t["progress"]}%'))
        except tk.TclError:
            pass

    def action_sort_queue(self, col):
        if not self.queue_rows:
            return
        asc = not (self.queue_sort[0] == col and self.queue_sort[1])
        self.queue_sort = (col, asc)
        keys = {
            "#": lambda t: 0,
            "status": lambda t: t["status"],
            "title": lambda t: t["tags"].get("title", ""),
            "artist": lambda t: t["tags"].get("artist", ""),
            "album": lambda t: t["tags"].get("album", ""),
            "folder": lambda t: t["tags"].get("folder", ""),
            "target_pl": lambda t: t.get("target_pl_name", ""),
            "size": lambda t: t["tags"].get("size", 0),
            "progress": lambda t: t["progress"],
        }
        if col == "#":
            self.queue_rows.sort(key=lambda t: self.queue_rows.index(t), reverse=not asc)
        else:
            self.queue_rows.sort(key=keys.get(col, lambda t: 0), reverse=not asc)
        self._render_queue()
        self._mark_sort_arrow(self.tree, col, asc)
        self._log(f"[=] 传输列表按『{col}』{'升序' if asc else '降序'}排列。")

    def on_view_change(self, event):
        self.view_mode = "compact" if self.view_combo.get() == "紧凑" else "details"
        small = {"#": 28, "status": 50, "title": 150, "artist": 90, "album": 100,
                 "folder": 100, "target_pl": 84, "size": 60, "progress": 70}
        normal = {"#": 34, "status": 62, "title": 210, "artist": 110, "album": 130,
                  "folder": 130, "target_pl": 106, "size": 78, "progress": 110}
        for c, w in (small if self.view_mode == "compact" else normal).items():
            self.tree.column(c, width=w)

    def action_start_transfer(self):
        """传输成功后，把带目标清单的曲目写入对应播放清单（不改源文件）。"""
        if not self.queue_rows:
            messagebox.showinfo("提示", "传输列表是空的，请先添加 MP3。")
            return
        pending = [i for i, row in enumerate(self.queue_rows)
                   if row["status"] in (base.STATUS_WAIT, base.STATUS_FAIL)]
        if not pending:
            messagebox.showinfo("提示", "没有待传输的条目（都是完成或传输中）。")
            return
        self.stop_flag.clear()
        # v39：标记当前是传输任务——进度弹窗据此显示“传完本首后停止”。
        self._transfer_active = True
        self._sync_stop_buttons("normal")

        def fn(d):
            total = len(pending)
            completed = 0
            ok = fail = skipped = 0
            pl_tids = {}       # plid -> [(tid, 名称)]
            self._log(f"[*] 开始传输，共 {total} 首待传。")
            # v37：传输前先让机器停止播放，避免传歌时继续放音占用总线。
            try:
                d.play_control(base.pde.NJB3Device.PLAY_STOP)
                self._log("[*] 已向机器发送停止播放指令。")
            except Exception as e:
                self._log(f"[!] 传输前停止播放指令未成功：{e}")
            for idx in pending:
                if self.stop_flag.is_set():
                    self._log("[!] 用户停止。")
                    break
                row = self.queue_rows[idx]
                name = row["tags"].get("filename", row["path"])
                self.q.put(("row_update", idx, base.STATUS_RUN, 0))
                self.q.put(("progress_stat", f"进度 0%   {completed}/{total}"))
                self._log(f"[→] 上传 {name} ...")

                # v36：同名+同字节数视为已存在，设备不会回反馈，直接复用机器曲目 ID。
                tid = None
                try:
                    local_size = os.path.getsize(row["path"])
                    local_name = os.path.basename(row["path"]).casefold()
                    for t in getattr(self, "tracks", []) or []:
                        try:
                            same_size = int(t.get("filesize", 0) or 0) == local_size
                        except (TypeError, ValueError):
                            same_size = False
                        same_name = str(t.get("filename", "")).casefold() == local_name
                        if same_size and same_name:
                            tid = t.get("trackid")
                            break
                except Exception:
                    tid = None
                if tid is not None:
                    skipped += 1
                    self._log(f"[=] 【{name}】机器中已存在同名同长度文件，跳过上传 (ID {tid})。")
                else:
                    def prog(done, total_bytes, i=idx, done0=completed, n=total):
                        pct = done * 100 // total_bytes if total_bytes else 0
                        self.q.put(("row_update", i, base.STATUS_RUN, pct))
                        self.q.put(("progress_stat", f"进度 {pct}%   {done0}/{n}"))
                    try:
                        # v39：补时长到 tags → 设备写入 0x0105，解决新歌时长空白/18:12:15。
                        self._ensure_track_length(row)
                        tid = d.upload_track(row["path"], row["tags"], progress=prog)
                        ok += 1
                        self._log(f"[OK] {name} 完成 (ID {tid})")
                    except Exception as e:
                        self.q.put(("row_update", idx, base.STATUS_FAIL, 0))
                        self._log(f"[×] {name} 失败：{e}")
                        fail += 1
                        continue
                self.q.put(("row_update", idx, base.STATUS_DONE, 100))
                completed += 1
                self.q.put(("progress_stat", f"进度 100%   {completed}/{total}"))
                plid = row.get("target_pl_id")
                if plid is not None and tid is not None:
                    pl_tids.setdefault(int(plid), []).append(
                        (int(tid), row.get("target_pl_name", str(plid))))

            # v37：0x0107 是 copy-on-write，只提交本次曲目会覆盖清单里原有歌曲。
            # 先读回原有成员，与新曲合并后一次性写入；本地缓存用全量替换。
            for plid, ids in pl_tids.items():
                trackids = [x[0] for x in ids]
                plname = ids[0][1]
                try:
                    newid, merged = self._append_playlist_keep_existing(
                        d, plid, trackids)
                    self.q.put(("pl_members_replace", plid, newid, merged))
                    self.q.put(("playlist_retarget", plid, newid))
                    self.q.put(("playlists", d.list_playlists()))
                    self._log(f"[OK] 已把 {len(trackids)} 首写入播放清单【{plname}】"
                              f"（含原有 {len(merged) - len(trackids)} 首，"
                              f"共 {len(merged)} 首；ID {plid} → {newid}）")
                except Exception as e:
                    self._log(f"[!] 写入播放清单【{plname}】失败：{e}")

            full = (not self.stop_flag.is_set()) and fail == 0
            self.q.put(("progress_stat",
                        f"完成   {completed}/{total}" + (f"  跳过 {skipped}" if skipped else "")))
            self.q.put(("transfer_finished", full, ok, fail, skipped))
            self._log(f"[=] 传输结束：成功 {ok}，失败 {fail}，已存在跳过 {skipped}。")

        self._worker(fn)

    def _build_devstat_module(self, parent):
        p = self.pal
        bar = ttk.Frame(parent, padding=(8, 6))
        bar.pack(fill="x", padx=4, pady=4)
        self.info_bar = bar

        main = ttk.Frame(bar)
        main.pack(fill="x")

        left = ttk.Frame(main)
        left.pack(side="left")
        ttk.Label(left, text="音量:").pack(side="left", padx=(0, 3))
        if not hasattr(self, "vol_var"):
            self.vol_var = tk.IntVar(value=50)
        self.vol_scale = ttk.Scale(left, from_=0, to=100, orient="horizontal",
                                   length=118, variable=self.vol_var,
                                   command=self.on_volume_change)
        self.vol_scale.pack(side="left")
        self.vol_lbl = ttk.Label(left, text="50", width=3, foreground=p["ok"])
        self.vol_lbl.pack(side="left", padx=(3, 0))
        self.vol_scale.bind("<ButtonRelease-1>", lambda e: self.action_set_volume())
        self.vol_scale.configure(state="disabled")
        if not hasattr(self, "eax_on_var"):
            self.eax_on_var = tk.BooleanVar(value=False)
        self.eax_chk = ttk.Checkbutton(left, text="EAX", variable=self.eax_on_var,
                                       command=self.action_eax_toggle, state="disabled")
        self.eax_chk.pack(side="left", padx=(10, 2))
        self.eax_widgets = (self.vol_scale, self.eax_chk)

        sep = ttk.Separator(main, orient="vertical")
        sep.pack(side="left", fill="y", padx=8)

        right = ttk.Frame(main)
        right.pack(side="left", fill="x", expand=True)
        self.info_right = right
        self.info_vars = {
            "cap": tk.StringVar(value="容量  -"),
            "free": tk.StringVar(value="可用  -"),
            "bat": tk.StringVar(value="电源  -"),
            "fw": tk.StringVar(value="固件  -"),
        }
        self.info_labels = {}
        for key in ("cap", "free", "bat", "fw"):
            self.info_labels[key] = ttk.Label(
                right, textvariable=self.info_vars[key],
                foreground=p["text_dim"], font=("Consolas", 9))
        self._layout_info_bar()

    def _build_playback_module(self, parent):
        ctlf = ttk.LabelFrame(parent, text=" 播放控制（通过 USB 控制播放器） ", padding=(8, 6))
        ctlf.pack(fill="x", padx=4, pady=4)
        self.ctrl_frame = ctlf
        if not getattr(self, "ctrl_buttons", None):
            pde = base.pde
            self.ctrl_buttons = [
                ("▶ 播放", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_START)),
                ("■ 停止", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_STOP)),
                ("⏸ 暂停", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_PAUSE)),
                ("⏵ 恢复", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_RESUME)),
                ("▶ 播放选中曲目", self.action_play_selected),
                ("查询状态", self.action_play_status),
            ]
        crow = ttk.Frame(ctlf)
        crow.pack(fill="x")
        self.ctrl_btn_row = crow
        self._ctrl_btn_mode = None
        crow.bind("<Configure>", lambda e: self._layout_ctrl_buttons())
        self._layout_ctrl_buttons()

    def _build_driver_module(self, parent):
        drv = ttk.Frame(parent, padding=(8, 6))
        drv.pack(fill="x", padx=4, pady=4)
        ttk.Label(drv, text="WinUSB 驱动装不上会导致连不上设备：",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        btnrow = ttk.Frame(drv)
        btnrow.pack(fill="x", pady=(5, 0))
        self.drv_btn2 = ttk.Button(btnrow, text="⚙ 安装/修复 WinUSB 驱动",
                                   command=self.action_install_driver)
        self.drv_btn2.pack(side="left", padx=2)
        ttk.Button(btnrow, text="查看当前驱动状态", command=self.action_driver_status).pack(
            side="left", padx=2)
        ttk.Button(btnrow, text="驱动说明/排错", command=self.action_driver_help).pack(
            side="left", padx=2)

    def action_refresh_full_library(self):
        """v46：手动强制完整读取曲库+清单（慢），并更新该机器序号的缓存。

        用于设备上的曲目有增删、或扩展元数据需要重建时；平时连接走快读缓存。
        """
        if not getattr(self, "connected", False) or getattr(self, "dev", None) is None:
            messagebox.showinfo("提示", "请先连接设备。")
            return
        if getattr(self, "worker_busy", False):
            messagebox.showinfo("提示", "当前有任务在运行，请等它结束再完整刷新。")
            return
        self._force_full_library_read = True
        self._log("[*] 手动完整刷新：正在逐首读取完整元数据（约需一分钟），请稍候 …")
        self.action_refresh_lib_playlists()

    def _build_refresh_module(self, parent):
        devf = ttk.Frame(parent, padding=(8, 6))
        devf.pack(fill="x", padx=4, pady=4)
        brow = ttk.Frame(devf)
        brow.pack(fill="x")
        self.refresh_info_btn = ttk.Button(brow, text="刷新设备信息",
                                           command=self.action_refresh_device_only)
        self.refresh_info_btn.pack(side="left", padx=2)
        self.refresh_lib_btn = ttk.Button(
            brow, text="刷新完整曲库和播放清单(速度慢)",
            command=self.action_refresh_full_library)
        self.refresh_lib_btn.pack(side="left", padx=2)
        ttk.Button(brow, text="设置机主名", command=self.action_owner).pack(side="left", padx=2)

    def _build_appearance_module(self, parent):
        skinf = ttk.Frame(parent, padding=(8, 6))
        skinf.pack(fill="x", padx=4, pady=4)
        srow = ttk.Frame(skinf)
        srow.pack(fill="x")
        ttk.Label(srow, text="当前皮肤：").pack(side="left")
        self.skin_name_var = tk.StringVar(
            value=getattr(self, "skin_name", theme.FOLLOW_SYSTEM))
        self.skin_combo = ttk.Combobox(
            srow, textvariable=self.skin_name_var,
            values=list(theme.theme_names()), state="readonly", width=14)
        self.skin_combo.pack(side="left", padx=(2, 6))
        self.skin_combo.bind("<<ComboboxSelected>>", self.on_skin_preset_changed)
        ttk.Button(srow, text="外观设置…", command=self.action_skin_settings).pack(
            side="left", padx=2)
        ttk.Button(srow, text="导入皮肤", command=self.action_skin_import).pack(
            side="left", padx=2)
        ttk.Button(srow, text="导出皮肤", command=self.action_skin_export).pack(
            side="left", padx=2)
        ttk.Button(srow, text="布局编辑器…", command=self.action_open_layout_editor).pack(
            side="left", padx=2)

    def _build_log_module(self, parent):
        logf = ttk.Frame(parent, padding=(4, 2))
        logf.pack(fill="both", expand=True, padx=4, pady=4)
        self.log = tk.Text(logf, height=4, state="disabled", font=("Consolas", 9),
                           wrap="word")
        logv = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=logv.set)
        self.log.pack(side="left", fill="both", expand=True)
        logv.pack(side="right", fill="y")

    # -------------------------------------------------------------- 复位
    def _ask_reset_layout(self):
        if not messagebox.askyesno("复位布局", "恢复默认工作台布局？当前摆放会被覆盖。"):
            return
        ids = list(self.dock.panels)
        self.dock.tree = self.studio_default_tree(ids)
        self.dock.floats, self.dock.hidden = {}, set()
        self.dock.save()
        self.dock.relayout()

        cids = list(self.control_dock.panels)
        self.control_dock.tree = control_default_tree(cids)
        self.control_dock.floats, self.control_dock.hidden = {}, set()
        self.control_dock.apply_content_mins()
        self.control_dock.set_panel_min_heights({"devstat": 80, "playback": 100, "driver": 90,
                                                 "refresh": 80, "appearance": 80, "log": 90})
        self.control_dock.save()
        self.control_dock.relayout()

        self.workbench_collapsed = {"explorer": False, "transfer": False}
        for side in ("explorer", "transfer"):
            self._remove_wb_handle(side)
        btn = getattr(self, "explorer_fold_btn", None)
        if btn is not None:
            btn.configure(text="«")
        btn = getattr(self, "transfer_fold_btn", None)
        if btn is not None:
            btn.configure(text="»")
        for title, body in ((getattr(self, "explorer_title", None),
                             getattr(self, "explorer_body", None)),
                            (getattr(self, "transfer_head", None),
                             getattr(self, "transfer_body", None))):
            if title is not None:
                if title is getattr(self, "explorer_title", None):
                    title.place(relx=0, rely=0, relwidth=1.0, height=20, bordermode="outside")
                    title.lift()
                else:
                    title.pack(fill="x")
            if body is not None:
                body.pack(fill="both", expand=True)
        for side in ("left", "right"):
            self._remove_pc_collapse_handle(side)
        self.pc_side_collapsed = {"left": False, "right": False}
        if getattr(self, "pc_left_body", None) is not None:
            self.pc_left_body.pack(fill="both", expand=True)
        if getattr(self, "pc_right_body", None) is not None:
            self.pc_right_body.pack(fill="both", expand=True)
        self.root.after(80, lambda: (self._push_workbench_sash(), self._pc_push_sash()))
        self._sync_main_view_buttons()


def main():
    root = tk.Tk()
    app = StudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
