#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_gui.py — Nomad Jukebox 3 图形界面（Win11 免安装版 v3）

界面结构（按设计图）：
  · 顶部：标题 + 状态栏 + 设备信息（固件/容量/可用/电源），窗体过窄时设备信息自动换行
  · 三个页签：传输列表 / 机器歌曲 / 其他功能
      - 传输列表：左=资源管理器式文件夹树，右=文件列表（点表头排序、切换查看类型），
                  可添加/移除/排序/改元数据，边传边整理；所有分栏可拖拽边界
      - 机器歌曲：左=机器内歌曲，右=播放清单，可拖拽左侧歌曲到右侧加入播放清单
      - 其他功能：驱动安装、日志、设备信息、改元数据、机主名等
  · 所有版块用 PanedWindow，支持拖拽边界线调整大小

线程模型：USB 操作串行在后台 worker 线程，主线程只做 UI，queue 事件驱动。
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import njb3_pde as pde
import njb3_layout as layout_cfg
import njb3_skin as skin_cfg
import usb.core   # 用于识别 USBError（设备失联/超时），与 pde 共用同一 pyusb

try:
    from njb3_transfer import read_id3
except Exception:
    read_id3 = None


# ---------------------------------------------------------------- 路径/提权
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(os.path.dirname(os.path.abspath(__file__)))


def package_root() -> Path:
    d = app_dir()
    if (d.parent / "01_驱动").is_dir():
        return d.parent
    return d


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_admin(script_arg: str = ""):
    import ctypes
    exe = sys.executable
    if getattr(sys, "frozen", False):
        target, params = exe, script_arg
    else:
        target = sys.executable
        params = f'"{os.path.abspath(__file__)}" {script_arg}'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, None, 1)
    return int(rc) > 32


# ---------------------------------------------------------------- helpers
def read_tags(path: str) -> dict:
    tags = {"title": Path(path).stem, "filename": Path(path).name,
            "codec": pde.CODEC_MP3, "size": os.path.getsize(path)}
    if read_id3:
        try:
            t = read_id3(path)
            for a in ("title", "artist", "album", "genre", "year", "track"):
                v = getattr(t, a, None)
                if v:
                    tags[a] = v
            if getattr(t, "length", None):
                tags["length"] = int(t.length)
        except Exception:
            pass
    return tags


STATUS_WAIT = "等待"
STATUS_RUN = "传输中"
STATUS_DONE = "完成"
STATUS_FAIL = "失败"

# 舞台放音模式的默认按键映射。keysym 用 Tk 的名称（空格=space，[=bracketleft）。
DEFAULT_STAGE_KEYS = {
    "play": "return",
    "pause_resume": "space",
    "stop": "escape",
    "prev": "bracketleft",
    "next": "bracketright",
    "select_up": "up",
    "select_down": "down",
    "go": "f5",
}
STAGE_ACTION_NAMES = {
    "play": "播放选中曲目",
    "pause_resume": "暂停 / 继续",
    "stop": "停止",
    "prev": "上一曲",
    "next": "下一曲",
    "select_up": "向上选择",
    "select_down": "向下选择",
    "go": "GO（从选中曲目开始整张清单）",
}


def stage_key_path() -> Path:
    return app_dir() / "stage_keys.json"


def load_stage_keys() -> dict:
    keys = dict(DEFAULT_STAGE_KEYS)
    p = stage_key_path()
    if p.exists():
        try:
            import json
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in data.items():
                if k in keys and v:
                    keys[k] = str(v)
        except Exception:
            pass
    return keys


def key_display(keysym: str) -> str:
    table = {
        "space": "空格", "return": "回车", "escape": "Esc",
        "bracketleft": "[", "bracketright": "]", "up": "↑", "down": "↓",
        "f5": "F5",
    }
    k = str(keysym).lower()
    return table.get(k, str(keysym))


class KeyMapDialog(tk.Toplevel):
    """舞台模式按键映射设置：点按钮后按任意键即完成映射。"""

    def __init__(self, master, keys: dict, on_saved):
        super().__init__(master)
        self.title("舞台按键映射")
        self.geometry("460x360")
        self.transient(master)
        self.keys = dict(keys)
        self.on_saved = on_saved
        self.capturing = None
        self.labels = {}
        f = ttk.Frame(self, padding=(12, 10))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="点击右侧按钮，然后按下想绑定的键：",
                  font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        for action, name in STAGE_ACTION_NAMES.items():
            row = ttk.Frame(f)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=name, width=30, anchor="w").pack(side="left")
            lab = ttk.Label(row, text=key_display(self.keys[action]), width=12,
                            foreground="#1a5fb4")
            lab.pack(side="left", padx=6)
            self.labels[action] = lab
            ttk.Button(row, text="修改…", width=8,
                       command=lambda a=action: self._begin(a)).pack(side="right")
        self.hint = ttk.Label(f, text="", foreground="#b00")
        self.hint.pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="恢复默认", command=self._reset).pack(side="left", padx=2)
        ttk.Button(bar, text="保存", command=self._save).pack(side="right", padx=2)
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=2)
        self.bind("<Key>", self._on_key)

    def _begin(self, action):
        self.capturing = action
        self.hint.configure(text=f"正在等待【{STAGE_ACTION_NAMES[action]}】的新按键，请按键盘…")

    def _on_key(self, event):
        if not self.capturing:
            return
        keysym = event.keysym or ""
        if keysym == "Escape":
            self.capturing = None
            self.hint.configure(text="已取消本次按键捕获。")
            return "break"
        self.keys[self.capturing] = keysym.lower()
        self.labels[self.capturing].configure(text=key_display(keysym))
        self.hint.configure(text=f"已设置为 {key_display(keysym)}，请继续修改或点保存。")
        self.capturing = None
        return "break"

    def _reset(self):
        self.keys = dict(DEFAULT_STAGE_KEYS)
        for a, lab in self.labels.items():
            lab.configure(text=key_display(self.keys[a]))
        self.hint.configure(text="已恢复默认映射，点保存生效。")

    def _save(self):
        try:
            import json
            stage_key_path().write_text(
                json.dumps(self.keys, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.on_saved(self.keys)
        self.destroy()


class SkinDialog(tk.Toplevel):
    """v27：外观设置对话框（参照 foobar2000 Quick Appearance Setup / Colors and Fonts）。

    · 左侧：内置预设一键切换，右侧：语义色 + 字体逐项调整；
    · 任何改动即时回调 on_preview 实时预览；
    · show() 返回 {'name','skin'} 表示确定，None 表示取消（由调用方还原）。
    """

    COLOR_LABELS = {
        "bg": "窗口背景", "fg": "正文文字",
        "select_bg": "焦点选中行底色", "select_fg": "焦点选中行文字",
        "idle_bg": "非焦点选中行底色", "idle_fg": "非焦点选中行文字",
        "stage_bg": "播放队列失焦底色", "stage_fg": "播放队列失焦文字",
        "accent": "强调色（标题/页签）", "ok": "正常色（音量）", "warn": "警示色（状态）",
    }

    def __init__(self, master, name: str, skin: dict, on_preview):
        super().__init__(master)
        self.title("外观设置 — 皮肤 / 配色 / 字体")
        self.geometry("660x560")
        self.transient(master)
        self.on_preview = on_preview
        self.name = name
        self.skin = skin_cfg.normalize_skin(skin)
        self.result = None
        self._build()

    def _build(self):
        f = ttk.Frame(self, padding=(12, 10))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="选择预设皮肤后可直接预览；也可在右侧逐项微调颜色与字体。",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        # 左：预设列表
        left = ttk.LabelFrame(body, text=" 预设皮肤（Quick Setup） ", padding=(8, 6))
        left.pack(side="left", fill="y", padx=(0, 8))
        self.preset_list = tk.Listbox(left, height=8, width=14,
                                      font=("Microsoft YaHei UI", 9),
                                      exportselection=False)
        self.preset_list.pack(fill="y")
        for nm in skin_cfg.preset_names():
            self.preset_list.insert("end", nm)
        if self.name in skin_cfg.preset_names():
            self.preset_list.selection_set(skin_cfg.preset_names().index(self.name))
        self.preset_list.bind("<<ListboxSelect>>", self._on_preset)

        # 右：逐项颜色 + 字体
        right = ttk.LabelFrame(body, text=" 颜色与字体（Colors and Fonts） ", padding=(8, 6))
        right.pack(side="left", fill="both", expand=True)
        self.color_vars = {}
        grid = ttk.Frame(right)
        grid.pack(fill="both", expand=True)
        for i, key in enumerate(skin_cfg.COLOR_KEYS):
            r, c = divmod(i, 2)
            cell = ttk.Frame(grid, padding=(4, 3))
            cell.grid(row=r, column=c, sticky="w")
            ttk.Label(cell, text=self.COLOR_LABELS[key], width=20,
                      anchor="w").pack(side="left")
            var = tk.StringVar(value=self.skin[key])
            self.color_vars[key] = var
            ent = ttk.Entry(cell, textvariable=var, width=9)
            ent.pack(side="left")
            ent.bind("<FocusOut>", lambda e, k=key: self._on_color(k))
            ent.bind("<Return>", lambda e, k=key: self._on_color(k))
            swatch = tk.Label(cell, text="  ", width=3, relief="ridge",
                              background=self.skin[key])
            swatch.pack(side="left", padx=(4, 0))
            self.color_vars[key + "_swatch"] = swatch

        frow = ttk.Frame(right, padding=(4, 8))
        frow.pack(fill="x")
        ttk.Label(frow, text="字体：").pack(side="left")
        self.font_var = tk.StringVar(value=self.skin["font_family"])
        ttk.Entry(frow, textvariable=self.font_var, width=20).pack(side="left")
        ttk.Label(frow, text="字号：").pack(side="left", padx=(8, 0))
        self.size_var = tk.IntVar(value=self.skin["font_size"])
        ttk.Spinbox(frow, from_=7, to=18, width=4,
                    textvariable=self.size_var, command=self._on_font).pack(side="left")
        ttk.Button(frow, text="应用字体", command=self._on_font).pack(side="left", padx=6)

        bar = ttk.Frame(f, padding=(0, 10, 0, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="恢复当前预设", command=self._reset_preset).pack(side="left")
        ttk.Button(bar, text="确定", command=self._ok).pack(side="right", padx=4)
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")

    def _on_preset(self, event=None):
        sel = self.preset_list.curselection()
        if not sel:
            return
        name = skin_cfg.preset_names()[sel[0]]
        self.name = name
        self.skin = skin_cfg.get_preset(name)
        for key in skin_cfg.COLOR_KEYS:
            self.color_vars[key].set(self.skin[key])
            self.color_vars[key + "_swatch"].configure(background=self.skin[key])
        self.font_var.set(self.skin["font_family"])
        self.size_var.set(self.skin["font_size"])
        if self.on_preview:
            self.on_preview(self.name, self.skin)

    def _on_color(self, key):
        raw = self.color_vars[key].get()
        fallback = self.skin[key]
        val = raw if (len(raw) == 7 and raw.startswith("#")
                      and self._is_hex(raw[1:])) else fallback
        self.color_vars[key].set(val)
        self.skin[key] = val
        self.color_vars[key + "_swatch"].configure(background=val)
        if self.on_preview:
            self.on_preview(self.name, self.skin)

    @staticmethod
    def _is_hex(s: str) -> bool:
        try:
            int(s, 16)
            return True
        except Exception:
            return False

    def _on_font(self):
        try:
            self.skin["font_family"] = self.font_var.get() or "Microsoft YaHei UI"
            self.skin["font_size"] = min(18, max(7, int(self.size_var.get())))
        except Exception:
            pass
        if self.on_preview:
            self.on_preview(self.name, self.skin)

    def _reset_preset(self):
        self.skin = skin_cfg.get_preset(self.name)
        for key in skin_cfg.COLOR_KEYS:
            self.color_vars[key].set(self.skin[key])
            self.color_vars[key + "_swatch"].configure(background=self.skin[key])
        if self.on_preview:
            self.on_preview(self.name, self.skin)

    def _ok(self):
        self.result = {"name": self.name, "skin": dict(self.skin)}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("NJB3 传输管理工具 — PDE 直连（无需 CMS5）")
        root.geometry("1120x760")
        root.minsize(900, 620)

        self.layout = layout_cfg.load_layout()
        # v27：界面皮肤（外观）——从 gui_skin.json 读取，缺省为经典浅色
        _skin_cfg = skin_cfg.load_config()
        self.skin_name = _skin_cfg["name"]
        self.skin = _skin_cfg["skin"]
        self.q = queue.Queue()
        # 通讯进度提示框：长任务显示，结束自动关闭；不是设备保活功能
        self.progress_win = None
        self.progress_after = None
        self.progress_title_var = tk.StringVar(value="正在与播放器通讯")
        self.progress_detail_var = tk.StringVar(value="请稍候 ...")
        self.dev = None            # 持久连接：一次 capture 后复用，直到用户主动断开
        self.connected = False
        self.worker_busy = False   # 仅主线程读写，提交任务时同步置位（不再靠队列异步）
        self.dev_lock = threading.Lock()   # 真正的串行锁：同一时刻只有一个 USB 任务
        self.stop_flag = threading.Event()
        self.queue_rows = []       # 传输列表: dict(path,tags,status,progress)
        self.tracks = []           # 机器曲库快照
        self.playlists = []        # 播放清单快照
        self.cur_playlist = None   # 当前选中的播放清单 id
        self.pl_tracks = []        # 当前播放清单内的曲目
        self.pl_members = {}       # playlistid -> list(trackid)，保留设备里的顺序和重复项

        # 排序/查看状态
        self.queue_sort = ("#", False)
        self.lib_sort = ("num", True)   # v11：曲库列头排序状态；num 为固定序号
        self.lib_order = []             # v11：按设备原始顺序的 trackid 列表（序号基准）
        self.pending_edits = {}         # v11：trackid -> {字段:新值}，暂存未同步修改
        self.view_mode = "details"  # details | compact
        # 曲库搜索定位（默认按标题）
        self.lib_search_var = tk.StringVar(value="")
        self.lib_search_field_var = tk.StringVar(value="标题")
        self.lib_search_only_var = tk.BooleanVar(value=False)
        # 播放进度滑动条状态（seek_var 保留以兼容旧测试/脚本）
        # v26★：进度条(play_pos_var) 与 “当前播放位置”标题(play_elapsed_sec) 解耦为两个模块：
        #   · play_pos_var    —— 滑动条/进度条；识别不到曲目（无真实总时长）时不走动；
        #   · play_elapsed_sec —— 标题“当前播放位置 XX:XX”；始终每秒走动，30s 向机器校正。
        self.play_pos_var = tk.DoubleVar(value=0.0)
        self.play_elapsed_sec = 0.0
        self.play_pos_label_var = tk.StringVar(value="00:00")
        self.play_duration_var = tk.StringVar(value="05:00")
        self.play_track_var = tk.StringVar(value="当前曲目：未知")
        self.seek_var = tk.StringVar(value="0")
        # 播放进度：libnjb 证实设备没有异步通知，只能轮询；UI 每秒模拟推进，
        # 每 30 秒（或播完/换曲时）才真正读一次设备，避免持续占用 USB。
        self.play_state = "unknown"       # playing / paused / stopped / ended / unknown
        self.play_track_id = None
        self.play_track_info = None
        self.play_queue_pos = None
        self._last_queue_pos = None
        self.play_duration_sec = 300
        # v23：总时长来源标记——记录最后一次自动同步所依据的曲目 trackid。
        # 同一曲目内用户手改的“总时长”不再被自动覆盖；换曲才重新取曲库时长。
        self._duration_synced_tid = None
        self.play_last_sync = 0.0
        self.play_sync_pending = False
        # v19：seek 目标与时刻，用于"seek 后主动同步 + 陈旧值保护窗"
        self.play_seek_at = 0.0
        self.play_seek_target = 0
        # v22：观测驱动播放模型（真机证设备会自行按队列前进）。
        # _obs_last 保存上一次 observe_playback() 快照，用于 advance_detected 判定自然换曲；
        # _auto_loaded 表示本次播放已装载整条队列（play_track队首+N×queue_track其余），
        #   为真时切曲只靠观测、绝不逐曲 play_track。
        self._obs_last = None
        self._auto_loaded = False
        self._was_playing = False
        self._boundary_handled = False   # 队尾/停止边界本轮是否已处理，防重入
        # v21：真机反馈“能调小不能调大”，回退到最初好用的 0-100 百分比直发方式。
        # 不再写 38 探测、不再做档位×4 换算；界面数值与发往机器的数值一致。
        self.volume_mode = "percent"
        self.volume_max = 100
        self.volume_probe_data = None
        # v20 播控模式总开关：默认关闭，下部窗体禁用；开启时需先清空机器队列
        self.pcmode_var = tk.BooleanVar(value=False)
        # 舞台放音 / 播控清单
        # 每个 track dict 附加两个键：
        #   _seq  = 固定索引信号（加入清单时分配，拖动排序不变）
        #   _mseq = 机器序号（写入机器“選定的音樂”队列后的位置；None=尚未写入）
        self.stage_tracks = []         # list[dict]，顺序就是播控清单顺序
        self.stage_seq_counter = 0     # 固定索引 _seq 的分配计数器
        self._mseq_cache = {}          # trackid -> 机器序号（本地记住，跨会话）
        self.stage_index = None
        self.stage_playlist_id = None  # 当前载入的机器播放清单 ID（保存排序用）
        self.stage_keys = load_stage_keys()
        self.stage_mode_var = tk.BooleanVar(value=False)
        # v18：libnjb 证实 series-3 的 PDE 协议【没有任何播放模式设置命令】
        # （0x0116 等是静态寄存器，写它机器不会切换）。因此 7 种模式全部由
        # 本程序控制：维护队列顺序 + 当前游标，曲目播完自动按模式切换下一首。
        self.PLAY_MODES = (
            ("GO 模式（按 GO 逐曲）", "go"),
            ("单曲（放完停止）", "single"),
            ("单曲重复（反复当前）", "repeat_one"),
            ("顺序播放（放完停止）", "normal"),
            ("循环播放（队列循环）", "repeat_all"),
            ("随机播放（随机一遍后停）", "shuffle"),
            ("随机重复（随机循环）", "shuffle_repeat"),
        )
        self.play_mode_var = tk.StringVar(value="GO 模式（按 GO 逐曲）")
        # 程序控制播放：随机模式的遍历顺序，None 表示按队列原序
        self._shuffle_order = None
        # v21：播放历史（trackid），用于“上一曲”回退
        self._play_history = []
        self._auto_advance_pos = 0
        self.stage_status_var = tk.StringVar(value="未载入清单")
        self._stage_drag_from = None
        self._capturing_key = False
        # v18 全局焦点跟踪：记录最后被鼠标点中的 Treeview，用于选中配色与“播放选中曲目”。
        self._last_focus_tree = None
        self._hot_rows = []
        self._setup_selection_styles()

        # v20：加载本地记住的机器序号映射（跨会话保留）
        try:
            _ids, self._mseq_cache = self._load_queue_cache()
        except Exception:
            self._mseq_cache = {}
        self._build_ui()
        self.root.bind_all("<Key>", self._on_stage_key)
        self._poll()
        self._tick_playback()
        # 关窗即断开，确保播放器退出同步状态、USB 可安全拔除，绝不遗留占用
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        """窗口关闭：释放设备后再退出。"""
        try:
            d = self.dev
            self.dev = None
            self.connected = False
            if d:
                d.recover()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ============================================================ UI 构建
    def _build_ui(self):
        r = self.root
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass

        # ---------- 顶部：标题 + 状态 + 操作 ----------
        top = ttk.Frame(r, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="NJB3 传输管理工具", font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="未连接 - 请确认好设备并以管理员身份运行驱动安装")
        self.status_lbl = ttk.Label(top, textvariable=self.status_var, foreground="#b00",
                                    font=("Microsoft YaHei UI", 9))
        self.status_lbl.pack(side="left", padx=14)
        # 音量控制：放在连接状态显示的右面（连接后才可操作）
        vol_box = ttk.Frame(top)
        vol_box.pack(side="right", padx=(4, 8))
        ttk.Label(vol_box, text="音量:").pack(side="left", padx=(0, 2))
        # v21：恢复 v17 验证可行的 0-100 百分比直发（开始好使的方式）。
        # 用户不需要刻度尺，只显示当前数值；去掉 v20 的“校准”按钮与档位换算。
        VOL_LEN = 112
        self.vol_var = tk.IntVar(value=50)
        self.vol_scale = ttk.Scale(vol_box, from_=0, to=100, orient="horizontal",
                                   length=VOL_LEN, variable=self.vol_var,
                                   command=self.on_volume_change)
        self.vol_scale.pack(side="left")
        self.vol_lbl = ttk.Label(vol_box, text="50", width=3, foreground="#060")
        self.vol_lbl.pack(side="left", padx=(2, 0))
        self.vol_scale.bind("<ButtonRelease-1>", lambda e: self.action_set_volume())
        self.vol_scale.configure(state="disabled")
        self.eax_on_var = tk.BooleanVar(value=False)
        self.eax_chk = ttk.Checkbutton(vol_box, text="EAX", variable=self.eax_on_var,
                                       command=self.action_eax_toggle, state="disabled")
        self.eax_chk.pack(side="left", padx=(8, 2))
        # v19：EAX 调参面板“不好用也没啥用”，取消“调参…”按钮与面板，仅保留勾选开关。
        # 以上按钮先创建，连接后由 _sync_conn_buttons 统一启用
        self.eax_widgets = (self.vol_scale, self.eax_chk)
        # v27：顶栏“安装/修复驱动”“设备信息”已删除——功能统一在【其他功能】页签：
        #   · 安装/修复 WinUSB 驱动  → 其他功能页（与旧顶栏同一回调 action_install_driver）
        #   · 刷新设备信息 / 刷新曲库和播放清单 → 其他功能页（各自独立）
        self.connect_btn = ttk.Button(top, text="连接设备", command=self.action_connect)
        self.connect_btn.pack(side="right", padx=3)
        self.disconnect_btn = ttk.Button(top, text="⏏ 断开设备", command=self.action_disconnect,
                                         state="disabled")
        self.disconnect_btn.pack(side="right", padx=3)

        # ---------- 设备信息条（固件/容量/可用/电源），自适应换行 ----------
        self.info_bar = ttk.Frame(r, padding=(10, 4))
        self.info_bar.pack(fill="x")
        self.info_vars = {
            "fw": tk.StringVar(value="固件  -"),
            "cap": tk.StringVar(value="容量  -"),
            "free": tk.StringVar(value="可用  -"),
            "bat": tk.StringVar(value="电源  -"),
        }
        self.info_labels = {}
        for k in ("fw", "cap", "free", "bat"):
            self.info_labels[k] = ttk.Label(self.info_bar, textvariable=self.info_vars[k],
                                            font=("Consolas", 10), foreground="#333")
        self._layout_info_bar()   # 根据宽度决定一行/两行
        self.root.bind("<Configure>", self._on_root_configure)

        # ---------- 下部整体：左=常驻资源管理器树，右=三个页签 ----------
        # v11 按用户要求重排：资源管理器树在整个窗口下部【左侧常驻】，
        # 不再只出现在传输页签里；右侧才是曲库/传输/其他三个页签。
        main_paned = ttk.PanedWindow(r, orient="horizontal")
        main_paned.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.main_paned = main_paned

        left = ttk.Frame(main_paned)
        main_paned.add(left, weight=2)
        self._build_explorer_panel(left)     # 常驻资源管理器（系统原生图标）

        right = ttk.Frame(main_paned)
        main_paned.add(right, weight=5)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

        self.tab_playctrl = ttk.Frame(self.nb)
        self.tab_machine = ttk.Frame(self.nb)
        self.tab_transfer = ttk.Frame(self.nb)
        self.tab_other = ttk.Frame(self.nb)
        tab_objs = {"播放控制": self.tab_playctrl, "机器歌曲": self.tab_machine,
                    "传输列表": self.tab_transfer, "其他功能": self.tab_other}
        for name in self.layout.get("tab_order", layout_cfg.DEFAULT_LAYOUT["tab_order"]):
            if name in tab_objs:
                self.nb.add(tab_objs[name], text=name)

        self._build_playctrl_tab(self.tab_playctrl)
        self._build_machine_tab(self.tab_machine)
        self._build_transfer_tab(self.tab_transfer)
        self._build_other_tab(self.tab_other)
        # v16：默认打开“播放控制”工作台页签
        try:
            self.nb.select(self.tab_playctrl)
        except Exception:
            pass
        # v24：控件建好、主题确定后再清一次 selected 映射（首次在 __init__ 早期
        # 会被加载的主题默认值覆盖），确保行 tag 配色真正生效。
        self._configure_tree_style_map()
        # v27：控件建好后应用皮肤（切主题会重建样式，必须在此之后）。
        self._apply_current_skin()
        self.root.after(200, self.apply_layout)

    # -------------------------------------------------- 常驻资源管理器面板
    def _build_explorer_panel(self, parent):
        """自建资源管理器：左侧目录树 + 右侧文件详细列表，全部为 Tk 原生控件。

        用户实测原生 Explorer 子窗口会抢焦点（机器歌曲搜索框打不了字），
        右键菜单和跨窗口拖拽也不好集成，因此恢复完全自控的双栏结构：
          · 左：快速访问 / 此电脑 / 懒加载子目录
          · 右：名称 / 大小 / 类型 / 修改时间，双击 MP3 或拖到传输列表导入
        """
        lf = ttk.LabelFrame(parent, text=" 资源管理器 ", padding=(4, 4))
        lf.pack(fill="both", expand=True)
        self.explorer_frame = lf
        self.explorer_status_var = tk.StringVar(value="右侧文件可拖到传输列表")
        self.native_explorer = None
        self.explorer_collapsed = False

        # v18：标题栏带折叠按钮，可把资源管理器缩进到侧边
        ex_title = ttk.Frame(lf); ex_title.pack(fill="x")
        self.explorer_fold_btn = ttk.Button(ex_title, text="«", width=3,
                                            command=self.action_explorer_fold)
        self.explorer_fold_btn.pack(side="left")
        ttk.Label(ex_title, text="目录 / 文件（可拖入传输列表）",
                  foreground="#456").pack(side="left", padx=4)

        # 可折叠的body容器
        ex_body = ttk.Frame(lf)
        ex_body.pack(fill="both", expand=True)
        self.explorer_body = ex_body

        paned = ttk.PanedWindow(ex_body, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self.explorer_paned = paned
        tree_box = ttk.Frame(paned)
        file_box = ttk.Frame(paned)
        paned.add(tree_box, weight=1)
        paned.add(file_box, weight=2)

        # ---- 左侧目录树 ----
        self._shell_icons = {}
        self.folder_tree = ttk.Treeview(tree_box, show="tree", selectmode="browse")
        self._init_shell_icons()
        tvsb = ttk.Scrollbar(tree_box, orient="vertical", command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=tvsb.set)
        thsb = ttk.Scrollbar(tree_box, orient="horizontal", command=self.folder_tree.xview)
        self.folder_tree.configure(xscrollcommand=thsb.set)
        self.folder_tree.grid(row=0, column=0, sticky="nsew")
        tvsb.grid(row=0, column=1, sticky="ns")
        thsb.grid(row=1, column=0, sticky="ew")
        tree_box.rowconfigure(0, weight=1); tree_box.columnconfigure(0, weight=1)
        self.folder_tree.bind("<<TreeviewSelect>>", self.on_folder_select)
        self.folder_tree.bind("<<TreeviewOpen>>", self.on_folder_open)
        self.folder_tree.bind("<Double-1>", self.on_explorer_double)
        self.folder_tree.bind("<Button-3>", self.on_explorer_context)
        self._loaded_dirs = set()
        self._init_folder_tree()

        # ---- 右侧文件详细栏 ----
        self.FILE_COLS = ("name", "size", "type", "mtime", "path")
        self.file_tree = ttk.Treeview(file_box, columns=self.FILE_COLS,
                                      show="headings", selectmode="extended", height=10)
        for c, h, w in (("name", "名称", 150), ("size", "大小", 70),
                        ("type", "类型", 72), ("mtime", "修改时间", 118), ("path", "", 0)):
            self.file_tree.heading(c, text=h)
            self.file_tree.column(c, width=w, anchor="w", stretch=(c == "name"))
        self.file_tree.column("path", width=0, stretch=False)
        fvsb = ttk.Scrollbar(file_box, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=fvsb.set)
        fhsb = ttk.Scrollbar(file_box, orient="horizontal", command=self.file_tree.xview)
        self.file_tree.configure(xscrollcommand=fhsb.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        fvsb.grid(row=0, column=1, sticky="ns")
        fhsb.grid(row=1, column=0, sticky="ew")
        file_box.rowconfigure(0, weight=1); file_box.columnconfigure(0, weight=1)
        self.file_tree.tag_configure("mp3", foreground="#1a5fb4")
        self.file_tree.bind("<Double-1>", self.on_file_double)
        self.file_tree.bind("<Button-3>", self.on_file_context)
        self.file_tree.bind("<ButtonPress-1>", self.on_file_drag_start)
        self.file_tree.bind("<B1-Motion>", self.on_file_drag_motion)
        self.file_tree.bind("<ButtonRelease-1>", self.on_file_drag_release)

        bar = ttk.Frame(ex_body, padding=(0, 4)); bar.pack(fill="x")
        self.path_var = tk.StringVar(value="")
        ttk.Entry(bar, textvariable=self.path_var).pack(side="left", fill="x",
                                                        expand=True, padx=(0, 3))
        ttk.Button(bar, text="加入此文件夹 MP3",
                   command=self.action_add_folder).pack(side="right")
        ttk.Label(ex_body, textvariable=self.explorer_status_var, foreground="#555",
                  wraplength=360).pack(fill="x", pady=(2, 0))

    def action_explorer_fold(self):
        """收起/展开资源管理器：收起后缩到主分隔条最左侧。"""
        try:
            if not self.explorer_collapsed:
                self.explorer_body.pack_forget()
                self.explorer_fold_btn.configure(text="»")
                self.explorer_collapsed = True
            else:
                self.explorer_body.pack(fill="both", expand=True)
                self.explorer_fold_btn.configure(text="«")
                self.explorer_collapsed = False
            self.root.after(30, self._push_main_sash)
        except Exception:
            pass

    def _push_main_sash(self):
        p = getattr(self, "main_paned", None)
        if p is None:
            return
        try:
            total = p.winfo_width()
            if total < 40:
                return
            p.sashpos(0, 44 if self.explorer_collapsed else int(total *
                      self.layout.get("sash", {}).get("main_explorer", 0.32)))
        except Exception:
            pass

    # ---- 自建资源管理器的文件栏与拖拽 ----
    def _populate_file_list(self, path: str):
        """把目录内容填到右侧文件栏（目录在前，文件在后）。"""
        if not hasattr(self, "file_tree"):
            return
        for i in self.file_tree.get_children():
            self.file_tree.delete(i)
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except Exception as exc:
            self.explorer_status_var.set(f"无法读取：{exc}")
            return
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

    def _selected_file_paths(self) -> list[str]:
        out = []
        for iid in self.file_tree.selection():
            vals = self.file_tree.item(iid, "values")
            if len(vals) >= 5:
                out.append(vals[4])
        return out

    def on_file_double(self, event):
        iid = self.file_tree.identify_row(event.y)
        if not iid:
            return
        vals = self.file_tree.item(iid, "values")
        if len(vals) < 5:
            return
        path = vals[4]
        if os.path.isdir(path):
            # 在左树里定位并展开对应目录
            node = "path::" + path
            if self.folder_tree.exists(node):
                self._ensure_children(node)
                self.folder_tree.selection_set(node)
                self.folder_tree.item(node, open=True)
                self.folder_tree.see(node)
                self.on_folder_select(None)
            return
        self._add_files_to_queue([path])

    def on_file_context(self, event):
        iid = self.file_tree.identify_row(event.y)
        if iid:
            self.file_tree.selection_set(iid)
        paths = self._selected_file_paths()
        m = tk.Menu(self.root, tearoff=0)
        if paths:
            m.add_command(label="加入传输列表",
                          command=lambda: self._add_files_to_queue(paths))
        m.add_command(label="加入此文件夹全部 MP3", command=self.action_add_folder)
        m.add_command(label="刷新", command=lambda: self.on_folder_select(None))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def on_file_drag_start(self, event):
        iid = self.file_tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.file_tree.selection():
            self.file_tree.selection_set(iid)
        self._drag_paths = [p for p in self._selected_file_paths()
                            if p.lower().endswith(".mp3") and os.path.isfile(p)]
        if not self._drag_paths:
            return
        self._drag_active = True
        self._drag_popup = tk.Toplevel(self.root)
        self._drag_popup.overrideredirect(True)
        self._drag_popup.attributes("-topmost", True)
        tk.Label(self._drag_popup, text=f"拖到传输列表释放（{len(self._drag_paths)} 首）",
                 bg="#FFF3B0", fg="#A40", relief="solid", borderwidth=1,
                 padx=6, pady=3).pack()
        self._drag_popup.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")

    def on_file_drag_motion(self, event):
        if not getattr(self, "_drag_active", False):
            return
        try:
            self._drag_popup.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
        except Exception:
            pass

    def on_file_drag_release(self, event):
        if not getattr(self, "_drag_active", False):
            return
        self._drag_active = False
        try:
            self._drag_popup.destroy()
        except Exception:
            pass
        self._drag_popup = None
        # 判断鼠标释放时是否位于传输列表表格上方（页签需在“传输列表”）
        target = self.root.winfo_containing(event.x_root, event.y_root)
        in_queue = False
        w = target
        while w is not None:
            if w is self.tree:
                in_queue = True
                break
            w = getattr(w, "master", None)
        if in_queue:
            self._add_files_to_queue(self._drag_paths)
        elif self._drag_paths:
            self.explorer_status_var.set("未放入传输列表：请先切到“传输列表”页签再拖入")

    def _init_shell_icons(self):
        """用 Windows SHGetFileInfoW 取系统真实图标，注册为 Tk 图片。"""
        try:
            import ctypes
            from ctypes import wintypes

            class SHFILEINFO(ctypes.Structure):
                _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                            ("dwAttributes", wintypes.DWORD),
                            ("szDisplayName", ctypes.c_wchar * 260),
                            ("szTypeName", ctypes.c_wchar * 80)]

            sh = ctypes.windll.shell32
            SHGFI_ICON, SHGFI_SMALLICON, SHGFI_OPENICON = 0x100, 0x1, 0x2

            def make_icon(path, open_=False):
                fi = SHFILEINFO()
                flags = SHGFI_ICON | SHGFI_SMALLICON | (SHGFI_OPENICON if open_ else 0)
                r = sh.SHGetFileInfoW(str(path), 0, ctypes.byref(fi), ctypes.sizeof(fi), flags)
                if not r or not fi.hIcon:
                    return None
                ico = ctypes.windll.user32.ImageList_GetIcon(fi.hIcon, 0, 0) if False else fi.hIcon
                # HICON -> Tk PhotoImage 需经 ICO 文件，复杂；改用系统内置位图近似
                return None

            self._make_icon = make_icon
        except Exception:
            self._make_icon = lambda *a: None
        # 先在树里用简单文本标识；图标通过 tag 的 image（若后续可取）增强
        self.folder_tree.tag_configure("folder", foreground="#1a5fb4")

    def _init_folder_tree(self):
        """初始化资源管理器树：快速访问 + 此电脑（磁盘盘符），懒加载子目录。"""
        if not hasattr(self, "folder_tree"):
            return
        self._loaded_dirs = set()
        for i in self.folder_tree.get_children():
            self.folder_tree.delete(i)
        rapid = self.folder_tree.insert("", "end", iid="__rapid__", text="⭐ 快速访问", open=True,
                                        tags=("folder",))
        home = os.path.expanduser("~")
        for name, p in (("音乐", os.path.join(home, "Music")),
                        ("下载", os.path.join(home, "Downloads")),
                        ("桌面", os.path.join(home, "Desktop")),
                        ("我的文档", home)):
            iid = "path::" + p
            self.folder_tree.insert(rapid, "end", iid=iid, text=name, values=(p,), tags=("folder",))
            self._add_dummy(p, iid)
        pc = self.folder_tree.insert("", "end", iid="__pc__", text="💻 此电脑", open=True,
                                     tags=("folder",))
        for c in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drv = f"{c}:\\"
            if os.path.exists(drv):
                iid = "path::" + drv
                self.folder_tree.insert(pc, "end", iid=iid, text=f"{c}: (本地磁盘)",
                                        values=(drv,), tags=("folder",))
                self._add_dummy(drv, iid)

    def on_explorer_double(self, event):
        """双击文件夹展开/收起（首次展开由 <<TreeviewOpen>> 懒加载）。"""
        iid = self.folder_tree.identify_row(event.y)
        if not iid:
            return
        if iid.startswith("path::"):
            self._ensure_children(iid)
            self.folder_tree.selection_set(iid)
            self.folder_tree.item(iid, open=not self.folder_tree.item(iid, "open"))
        elif iid.startswith("file::"):
            self._add_files_to_queue([iid[6:]])

    def on_explorer_context(self, event):
        """资源管理器右键菜单。"""
        iid = self.folder_tree.identify_row(event.y)
        if not iid:
            return
        self.folder_tree.selection_set(iid)
        m = tk.Menu(self.root, tearoff=0)
        if iid.startswith("path::"):
            m.add_command(label="打开（展开）", command=lambda: (self._ensure_children(iid),
                                                           self.folder_tree.item(iid, open=True)))
            m.add_command(label="加入此文件夹 MP3", command=self.action_add_folder)
            m.add_command(label="刷新", command=lambda: self.reload_folder(iid))
        elif iid.startswith("file::"):
            m.add_command(label="加入传输列表", command=lambda: self._add_files_to_queue([iid[6:]]))
        else:
            m.add_command(label="刷新", command=self._init_folder_tree)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _add_files_to_queue(self, files):
        """把本地文件加入传输列表（供资源管理器双击/右键调用）。"""
        added = 0
        for f in files:
            if not f.lower().endswith(".mp3") or not os.path.isfile(f):
                continue
            try:
                tags = read_tags(f)
            except Exception:
                continue
            tags["folder"] = tags.get("folder") or os.path.basename(os.path.dirname(f))
            self.queue_rows.append({"path": f, "tags": tags, "status": STATUS_WAIT, "progress": 0,
                                     "seq": len(self.queue_rows) + 1})
            added += 1
        self._render_queue()
        self._log(f"[+] 从资源管理器加入 {added} 首到传输列表。")

    def _layout_info_bar(self):
        """窗体过窄时设备信息换成两行。"""
        for w in self.info_bar.winfo_children():
            w.pack_forget()
        width = self.root.winfo_width()
        if width and width < 820:
            # 两行
            row1, row2 = ttk.Frame(self.info_bar), ttk.Frame(self.info_bar)
            row1.pack(fill="x"); row2.pack(fill="x")
            self.info_labels["fw"].pack(in_=row1, side="left", padx=(0, 18))
            self.info_labels["cap"].pack(in_=row1, side="left", padx=(0, 18))
            self.info_labels["free"].pack(in_=row2, side="left", padx=(0, 18))
            self.info_labels["bat"].pack(in_=row2, side="left")
        else:
            for k in ("fw", "cap", "free", "bat"):
                self.info_labels[k].pack(side="left", padx=(0, 24))

    def _on_root_configure(self, event):
        if event.widget is not self.root:
            return
        w = self.root.winfo_width()
        cur = getattr(self, "_last_bar_rows", None)
        rows = 2 if w < 820 else 1
        if cur != rows:
            self._last_bar_rows = rows
            self._layout_info_bar()

    # ---------------------------------------------------------- 传输列表页签
    def _build_transfer_tab(self, parent):
        # v11：文件夹树已移到窗口下部【左侧常驻】，本页签不再重复；
        # 右侧按钮改为【竖向排列】，避免横向挤成一团。
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        tf = ttk.LabelFrame(outer, text=" 传输列表（点表头排序，可边传边增删减/改元数据） ", padding=(6, 5))
        tf.pack(side="left", fill="both", expand=True)

        cols = ("#", "status", "title", "artist", "album", "folder", "size", "progress")
        heads = ("#", "状态", "标题", "艺人", "专辑", "分类文件夹", "大小", "进度")
        widths = (34, 62, 210, 110, 130, 130, 78, 110)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended", height=12)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h, command=lambda cc=c: self.action_sort_queue(cc))
            self.tree.column(c, width=w, anchor="w")
        self.tree.column("#", width=34, anchor="center")
        self.tree.column("status", width=62, anchor="center")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double_click)
        # 拖拽调整行顺序（传输列表内部）
        self.tree.bind("<ButtonPress-1>", self.on_queue_drag_start)
        self.tree.bind("<B1-Motion>", self.on_queue_drag_motion)

        # v11：操作按钮【竖排】放在传输表格右侧，不再横排挤在一起
        btn_col = ttk.Frame(outer)
        btn_col.pack(side="right", fill="y", padx=(6, 0))
        ttk.Label(btn_col, text="操作", font=("Microsoft YaHei UI", 9, "bold")).pack(pady=(0, 4))
        for txt, cmd in (("＋ 添加 MP3", self.action_add_files),
                         ("移除选中", self.action_remove_sel),
                         ("清空列表", self.action_clear),
                         ("↑ 上移", lambda: self.action_move(-1)),
                         ("↓ 下移", lambda: self.action_move(1)),
                         ("导入 m3u", self.action_import_m3u)):
            ttk.Button(btn_col, text=txt, command=cmd).pack(fill="x", pady=2)
        ttk.Label(btn_col, text="查看", font=("Microsoft YaHei UI", 9, "bold")).pack(pady=(10, 4))
        self.view_combo = ttk.Combobox(btn_col, values=("详细信息", "紧凑"), state="readonly", width=12)
        self.view_combo.set("详细信息")
        self.view_combo.bind("<<ComboboxSelected>>", self.on_view_change)
        self.view_combo.pack(fill="x", pady=2)
        ttk.Label(btn_col, text="传输", font=("Microsoft YaHei UI", 9, "bold")).pack(pady=(10, 4))
        self.start_btn = ttk.Button(btn_col, text="▶ 开始传输", command=self.action_start_transfer)
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ttk.Button(btn_col, text="■ 停止", command=self.action_stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)

        # 元数据编辑（行内，双击载入）
        ed = ttk.LabelFrame(outer, text=" 选中项元数据（双击列表行载入，改完点“应用到选中”） ", padding=(6, 4))
        ed.pack(side="bottom", fill="x", pady=(6, 0))
        self.ed = {}
        fields = [("title", "标题", 18), ("artist", "艺人", 12), ("album", "专辑", 14),
                  ("genre", "流派", 10), ("year", "年份", 6), ("folder", "分类文件夹", 14)]
        row1 = ttk.Frame(ed); row1.pack(fill="x")
        for key, lab, w in fields:
            ttk.Label(row1, text=lab + ":").pack(side="left", padx=(5, 2))
            v = tk.StringVar()
            ttk.Entry(row1, textvariable=v, width=w).pack(side="left", padx=(0, 5))
            self.ed[key] = v
        ttk.Button(row1, text="应用到选中", command=self.action_apply_edit).pack(side="right", padx=4)

    def _add_dummy(self, path, iid):
        try:
            if any(os.path.isdir(os.path.join(path, x)) for x in os.listdir(path)):
                self.folder_tree.insert(iid, "end", iid=iid + "::dummy", text="loading")
        except Exception:
            pass

    def _ensure_children(self, iid):
        """懒加载子目录；只加载一次，不因反复选中/双击而清空已有节点。"""
        if not iid.startswith("path::") or iid.endswith("::dummy"):
            return
        if iid in getattr(self, "_loaded_dirs", set()):
            return
        path = iid[len("path::"):]
        self.folder_tree.delete(*self.folder_tree.get_children(iid))
        try:
            names = sorted(os.listdir(path))
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

    def on_folder_open(self, event):
        """点击展开箭头时触发：首次展开才加载子目录。"""
        iid = self.folder_tree.focus()
        if not iid and self.folder_tree.selection():
            iid = self.folder_tree.selection()[0]
        self._ensure_children(iid)

    def on_folder_select(self, event):
        """选中目录只刷新右侧文件栏，绝不清空树节点（否则双击展开会被收起）。"""
        sel = self.folder_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.startswith("path::") or iid.endswith("::dummy"):
            return
        path = iid[len("path::"):]
        try:
            self.path_var.set(path)
        except Exception:
            pass
        self._populate_file_list(path)

    def reload_folder(self, iid=None):
        """显式刷新：只重建指定节点的子项，保留展开状态。"""
        if iid is None:
            sel = self.folder_tree.selection()
            if not sel:
                self._init_folder_tree()
                return
            iid = sel[0]
        self._loaded_dirs.discard(iid)
        was_open = bool(self.folder_tree.item(iid, "open")) if self.folder_tree.exists(iid) else False
        self._ensure_children(iid)
        self.folder_tree.item(iid, open=was_open)
        self.on_folder_select(None)

    def action_add_folder(self, path: str | None = None):
        """把指定文件夹下的 MP3 加入传输列表。"""
        if path is None:
            sel = self.folder_tree.selection() if self.folder_tree is not None else ()
            if not sel:
                # 没选目录时退回到地址栏里手填的路径
                path = self.path_var.get().strip()
                if not path:
                    messagebox.showinfo("提示", "请先在左侧文件夹树里选中一个文件夹。")
                    return
            else:
                iid = sel[0]
                if not iid.startswith("path::"):
                    messagebox.showinfo("提示", "请选中一个具体的文件夹（不是“快速访问/此电脑”）。")
                    return
                path = iid[len("path::"):]
        if not path or not os.path.isdir(path):
            messagebox.showinfo("提示", f"文件夹不可用：{path}")
            return
        recursive = messagebox.askyesno("添加范围", f"添加文件夹【{path}】下的 MP3。\n\n是否包含子文件夹（递归）？")
        files = []
        try:
            if recursive:
                for root, _, fs in os.walk(path):
                    files += [os.path.join(root, f) for f in fs if f.lower().endswith(".mp3")]
            else:
                files = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(".mp3")]
        except Exception as e:
            self._log(f"[×] 扫描文件夹失败：{e}")
            return
        n = 0
        for f in files:
            try:
                tags = read_tags(f)
            except Exception:
                continue
            folder = os.path.basename(os.path.dirname(f))
            tags["folder"] = tags.get("folder") or folder
            self.queue_rows.append({"path": f, "tags": tags, "status": STATUS_WAIT, "progress": 0})
            n += 1
        self._render_queue()
        self._log(f"[+] 从文件夹加入 {n} 首到传输列表。")

    # ---------------------------------------------------------- 机器歌曲页签
    # 曲库列定义（v11）：# 为固定序号，其余列可点击表头排序
    LIB_COLS = ("num", "id", "title", "artist", "album", "genre", "folder", "size", "length", "trackno")
    LIB_HEADS = ("#", "ID", "标题", "艺人", "专辑", "流派", "文件夹", "大小", "时长", "曲号")
    LIB_WIDTHS = (42, 60, 200, 110, 130, 90, 130, 80, 60, 50)
    # 可双击编辑的字段 -> 数据结构 key
    LIB_EDITABLE = {"title": "title", "artist": "artist", "album": "album",
                    "genre": "genre", "folder": "folder", "trackno": "trackno",
                    "year": "year", "length": "length"}

    # -------------------------------------------------- 播放控制工作台（v16）
    def _build_playctrl_tab(self, parent):
        """三栏播放工作台：左=播放清单+列表内歌曲，中=播放控制+播放队列，右=机器内歌曲。
        左右两侧可折叠缩进到各自侧边；两侧歌曲均可拖入中间“播放队列”并排序。
        """
        # 用水平 PanedWindow：三栏都可拖动调宽，且都不会被兄弟栏挤成 0 宽。
        root = ttk.PanedWindow(parent, orient="horizontal")
        root.pack(fill="both", expand=True)
        self.pc_root = root

        # ================= 左栏（可折叠）=================
        self.pc_left = ttk.Frame(root)
        root.add(self.pc_left, weight=1)
        lb = ttk.Frame(self.pc_left); lb.pack(fill="x")
        self.pc_left_title = ttk.Label(lb, text=" 播放清单 / 列表内歌曲 ",
                                       font=("Microsoft YaHei UI", 9, "bold"))
        self.pc_left_title.pack(side="left")
        self.pc_left_fold_btn = ttk.Button(lb, text="«", width=3,
                                           command=self.action_pc_fold_left)
        self.pc_left_fold_btn.pack(side="right")
        self.pc_left_body = ttk.Frame(self.pc_left)
        self.pc_left_body.pack(fill="both", expand=True)

        pf = ttk.LabelFrame(self.pc_left_body, text=" 播放清单 ", padding=(4, 3))
        pf.pack(fill="both", expand=True)
        self.pl_tree = ttk.Treeview(pf, show="tree", selectmode="browse", height=4)
        self.pl_tree.pack(fill="both", expand=True)
        self.pl_tree.bind("<<TreeviewSelect>>", self.on_playlist_select)
        self.pl_tree.bind("<Double-1>", self.on_playlist_double)
        self.pl_tree.bind("<Button-3>", self.on_playlist_context)
        pbar = ttk.Frame(pf, padding=(0, 3)); pbar.pack(fill="x")
        for txt, cmd in (("新建", self.action_new_playlist), ("改名", self.action_rename_playlist),
                         ("刷新", self.action_refresh_playlists), ("删除", self.action_delete_playlist)):
            ttk.Button(pbar, text=txt, command=cmd).pack(side="left", padx=1)

        # 列表内的歌曲：可拖入中间播放队列
        pit = ttk.LabelFrame(self.pc_left_body, text=" 列表内的歌曲（拖到中间加入播放队列） ",
                             padding=(4, 3))
        pit.pack(fill="both", expand=True, pady=(5, 0))
        self.pl_songs = ttk.Treeview(pit, columns=("num", "title", "artist", "id"),
                                     show="headings", height=6, selectmode="extended")
        for c, h, w in (("num", "#", 34), ("title", "标题", 150),
                        ("artist", "艺人", 100), ("id", "ID", 56)):
            self.pl_songs.heading(c, text=h); self.pl_songs.column(c, width=w, anchor="w")
        plv = ttk.Scrollbar(pit, orient="vertical", command=self.pl_songs.yview)
        self.pl_songs.configure(yscrollcommand=plv.set)
        self.pl_songs.pack(side="left", fill="both", expand=True)
        plv.pack(side="right", fill="y")
        self.pl_songs.bind("<ButtonPress-1>", self.on_pl_songs_drag_start)
        self.pl_songs.bind("<B1-Motion>", self.on_pl_songs_drag_motion)
        self.pl_songs.bind("<ButtonRelease-1>", self.on_pl_songs_drag_release)

        # ================= 中栏 =================
        self.pc_mid = ttk.Frame(root)
        root.add(self.pc_mid, weight=3)
        expand_bar = ttk.Frame(self.pc_mid); expand_bar.pack(fill="x")
        self.pc_expand_left_btn = ttk.Button(expand_bar, text="» 展开播放清单",
                                             command=self.action_pc_unfold_left)
        self.pc_expand_right_btn = ttk.Button(expand_bar, text="展开机器内歌曲 «",
                                              command=self.action_pc_unfold_right)
        # 默认不显示，折叠侧栏后才显示

        # ---- 播放控制（原“其他功能”页签迁入）----
        # v24★：固件无法回读“当前曲目/总时长”，这些信息只来自本地曲库缓存匹配，
        # 故【当前曲目/进度条/总时长/刻度】整体上移到【播控模式】框体内显示；
        # 本框体只保留 6 个 USB 控制按钮 + 标题里动态显示“当前播放位置 XX:XX”
        # （已播秒数来自设备 elapsed，是真实回读值）。
        ctlf = ttk.LabelFrame(self.pc_mid, text=" 播放控制（通过 USB 控制播放器） ", padding=(6, 5))
        ctlf.pack(fill="x", pady=(2, 4))
        self.ctrl_frame = ctlf
        # v24：移除“上一曲/下一曲”（固件无此命令，程序模拟不可靠），恢复 6 个按钮；
        # 根据框体宽度自适应：1 行 6 个 / 2 行各 3 个。
        crow = ttk.Frame(ctlf); crow.pack(fill="x")
        self.ctrl_btn_row = crow
        self.ctrl_buttons = [
            ("▶ 播放", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_START)),
            ("■ 停止", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_STOP)),
            ("⏸ 暂停", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_PAUSE)),
            ("⏵ 恢复", lambda: self.action_play_ctrl(pde.NJB3Device.PLAY_RESUME)),
            ("▶ 播放选中曲目", self.action_play_selected),
            ("查询状态", self.action_play_status),
        ]
        crow.bind("<Configure>", lambda e: self._layout_ctrl_buttons())
        self._layout_ctrl_buttons()   # 先建出 6 个按钮，Configure 后再自适应

        # ---- v20 播控模式（原“播放队列/选定音乐”）----
        # 默认下部窗体禁用；须先点开关按钮清空机器队列后启用。
        sf = ttk.LabelFrame(self.pc_mid, text=" 播控模式 ", padding=(6, 5))
        sf.pack(fill="both", expand=True, pady=(2, 0))
        self.pc_mode_frame = sf
        # 总开关按钮：清空机器内现有“选定的音乐”并开启软件控制的播控模式
        self.pcmode_btn = ttk.Button(
            sf, text="清空机器内现有选定的音乐并开启软件控制的播控模式",
            command=self.action_pcmode_enable)
        self.pcmode_btn.pack(fill="x", pady=(2, 4))
        # v24★：从“播放控制”框体迁入的播放信息区（当前曲目 + 进度条 + 刻度）
        pinfo = ttk.Frame(sf); pinfo.pack(fill="x", pady=(2, 4))
        ttk.Label(pinfo, textvariable=self.play_track_var, foreground="#1a5fb4",
                  wraplength=560, justify="left").pack(anchor="w", padx=4)
        prow = ttk.Frame(pinfo); prow.pack(fill="x", pady=(5, 0))
        ttk.Label(prow, text="播放位置:").pack(side="left", padx=(2, 3))
        ttk.Label(prow, textvariable=self.play_pos_label_var, width=7,
                  font=("Consolas", 10, "bold")).pack(side="left")
        ttk.Label(prow, text="总时长:").pack(side="left", padx=(10, 3))
        ttk.Entry(prow, textvariable=self.play_duration_var, width=7).pack(side="left")
        ttk.Label(prow, text="mm:ss").pack(side="left", padx=(3, 8))
        ttk.Button(prow, text="定位", command=self.action_seek).pack(side="left", padx=2)
        self.play_scale = ttk.Scale(pinfo, from_=0, to=300, orient="horizontal",
                                    variable=self.play_pos_var,
                                    command=self.on_play_scale_change)
        self.play_scale.pack(fill="x", padx=4, pady=(4, 0))
        self.play_scale.bind("<ButtonRelease-1>", lambda e: self.action_seek())
        self.play_duration_var.trace_add("write", lambda *_: self._update_play_ticks())
        # v18 进度标尺：刻度线与时间标签按真实时间位置对齐，以滑块中心为对接点。
        self.play_ticks = ttk.Frame(pinfo, height=30)
        self.play_ticks.pack(fill="x", padx=4)
        self.play_ticks.pack_propagate(False)
        self.play_tick_canvas = tk.Canvas(self.play_ticks, height=10, highlightthickness=0)
        self.play_tick_canvas.pack(fill="x")
        self.play_tick_canvas.bind("<Configure>", lambda e: self._layout_play_ruler())
        self.play_tick_labels = []
        for _ in range(5):
            lab = ttk.Label(self.play_ticks, text="00:00", font=("Consolas", 8),
                            foreground="#666", anchor="center", width=5)
            self.play_tick_labels.append(lab)
        self._update_play_ticks()
        self.playstat_var = tk.StringVar(value="状态：未查询")
        ttk.Label(pinfo, textvariable=self.playstat_var, foreground="#333").pack(
            anchor="w", padx=4, pady=(2, 0))

        # 下部窗体容器：默认整体禁用，启用播控模式后才开放
        body = ttk.Frame(sf)
        body.pack(fill="both", expand=True)
        self.pc_mode_body = body
        qrow1 = ttk.Frame(body); qrow1.pack(fill="x", pady=(0, 4))
        ttk.Button(qrow1, text="↻ 读取清单", command=self.action_stage_read_queue).pack(
            side="left", padx=2)
        ttk.Button(qrow1, text="载入机器清单", command=self.action_stage_load_playlist).pack(
            side="left", padx=2)
        ttk.Button(qrow1, text="加入选中曲目", command=self.action_stage_add_selected).pack(
            side="left", padx=2)
        ttk.Label(qrow1, text="机器清单:").pack(side="left", padx=(8, 3))
        self.stage_pl_var = tk.StringVar(value="")
        self.stage_pl_combo = ttk.Combobox(qrow1, textvariable=self.stage_pl_var,
                                           state="readonly", width=14)
        self.stage_pl_combo.pack(side="left", padx=2)
        qrow2 = ttk.Frame(body); qrow2.pack(fill="x", pady=(0, 4))
        ttk.Button(qrow2, text="★ GO", command=self.action_stage_go).pack(side="left", padx=2)
        ttk.Button(qrow2, text="⏮ 上一曲", command=lambda: self.action_stage_step(-1)).pack(side="left", padx=2)
        ttk.Button(qrow2, text="⏭ 下一曲", command=lambda: self.action_stage_step(1)).pack(side="left", padx=2)
        ttk.Button(qrow2, text="暂停/继续", command=self.action_stage_toggle).pack(side="left", padx=2)
        ttk.Button(qrow2, text="■ 停止", command=self.action_stage_stop).pack(side="left", padx=2)
        ttk.Button(qrow2, text="写回队列", command=self.action_stage_save_queue).pack(side="left", padx=2)
        ttk.Button(qrow2, text="重刷队列", command=self.action_stage_rebuild_queue).pack(side="left", padx=2)
        ttk.Button(qrow2, text="清空队列", command=self.action_stage_clear).pack(side="left", padx=2)
        ttk.Checkbutton(qrow2, text="舞台按键", variable=self.stage_mode_var,
                        command=self._sync_stage_mode).pack(side="left", padx=(6, 2))
        ttk.Button(qrow2, text="按键映射…", command=self.action_stage_keymap).pack(side="left", padx=2)
        # 播放模式控制行：全程序控制，选择即生效
        qrow3 = ttk.Frame(body); qrow3.pack(fill="x", pady=(0, 4))
        ttk.Label(qrow3, text="播放模式:").pack(side="left", padx=(2, 3))
        mode_cb = ttk.Combobox(qrow3, textvariable=self.play_mode_var, state="readonly", width=26,
                               values=[x[0] for x in self.PLAY_MODES])
        mode_cb.pack(side="left", padx=2)
        mode_cb.bind("<<ComboboxSelected>>", self.on_play_mode_change)
        ttk.Label(qrow3, text="（程序自动控制）", foreground="#1a5fb4").pack(side="left", padx=(2, 0))
        self.play_mode_var.trace_add("write", lambda *_: self._sync_stage_mode())
        ttk.Label(body, textvariable=self.stage_status_var, foreground="#1a5fb4").pack(anchor="w")
        self.stage_pl_combo.bind("<<ComboboxSelected>>", self.on_stage_combo_select)
        # v20：新增“机器序号”列。num=固定索引信号(_seq，拖动不变)，mseq=机器序号(_mseq)
        cols = ("num", "mseq", "title", "artist", "length")
        self.stage_tree = ttk.Treeview(body, columns=cols, show="headings", height=6,
                                       selectmode="browse", style="Queue.Treeview")
        for c, h, w in zip(cols, ("#", "机器序号", "标题", "艺人", "时长"),
                           (34, 62, 210, 120, 58)):
            self.stage_tree.heading(c, text=h)
            self.stage_tree.column(c, width=w, anchor="w")
        self.stage_tree.column("num", width=34, anchor="center")
        self.stage_tree.column("mseq", width=62, anchor="center")
        stv = ttk.Scrollbar(body, orient="vertical", command=self.stage_tree.yview)
        self.stage_tree.configure(yscrollcommand=stv.set)
        self.stage_tree.pack(side="left", fill="both", expand=True, pady=(4, 0))
        stv.pack(side="right", fill="y", pady=(4, 0))
        self.stage_tree.bind("<ButtonPress-1>", self.on_stage_drag_start)
        self.stage_tree.bind("<B1-Motion>", self.on_stage_drag_motion)
        self.stage_tree.bind("<ButtonRelease-1>", self.on_stage_drag_release)
        self.stage_tree.bind("<<TreeviewSelect>>", self.on_stage_select)
        self.stage_tree.bind("<Double-1>", self.on_stage_double_click)
        # 默认禁用下部窗体
        self._set_pcmode_body_state("disabled")

        # ================= 右栏（可折叠）：机器内歌曲 =================
        self.pc_right = ttk.Frame(root)
        root.add(self.pc_right, weight=2)
        rb = ttk.Frame(self.pc_right); rb.pack(fill="x")
        self.pc_right_fold_btn = ttk.Button(rb, text="»", width=3,
                                            command=self.action_pc_fold_right)
        self.pc_right_fold_btn.pack(side="left")
        self.pc_right_title = ttk.Label(rb, text=" 机器内歌曲 ",
                                        font=("Microsoft YaHei UI", 9, "bold"))
        self.pc_right_title.pack(side="left")
        self.pc_right_body = ttk.Frame(self.pc_right)
        self.pc_right_body.pack(fill="both", expand=True)

        self.search_bar = ttk.Frame(self.pc_right_body, padding=(0, 0, 0, 4))
        if self.layout.get("show_lib_search", True):
            self.search_bar.pack(fill="x")
            ttk.Label(self.search_bar, text="搜索:").pack(side="left", padx=(0, 3))
            ttk.Entry(self.search_bar, textvariable=self.lib_search_var,
                      width=14).pack(side="left", padx=(0, 3))
            field = ttk.Combobox(self.search_bar, textvariable=self.lib_search_field_var,
                                 values=("标题", "艺人", "专辑", "ID", "文件名"),
                                 state="readonly", width=7)
            field.pack(side="left", padx=(0, 3))
            ttk.Button(self.search_bar, text="定位", command=self.action_find_lib).pack(
                side="left", padx=(0, 3))
            ttk.Checkbutton(self.search_bar, text="只显示匹配",
                            variable=self.lib_search_only_var,
                            command=lambda: self._render_lib()).pack(side="left")
        lf = ttk.LabelFrame(self.pc_right_body, text=" 机器内歌曲（拖到中间加入播放队列） ",
                            padding=(4, 3))
        lf.pack(fill="both", expand=True)
        self.lib_tree = ttk.Treeview(lf, columns=self.LIB_COLS, show="headings",
                                     height=10, selectmode="extended")
        for c, h, w in zip(self.LIB_COLS, self.LIB_HEADS, self.LIB_WIDTHS):
            self.lib_tree.heading(c, text=h, command=lambda cc=c: self.action_sort_lib(cc))
            self.lib_tree.column(c, width=w, anchor="w")
        self.lib_tree.column("num", width=40, anchor="center")
        self.lib_tree.tag_configure("dirty", background="#FFF3B0")
        self.lib_tree.tag_configure("changed", background="#FFF3B0", foreground="#D00")
        lvsb = ttk.Scrollbar(lf, orient="vertical", command=self.lib_tree.yview)
        self.lib_tree.configure(yscrollcommand=lvsb.set)
        hvsb = ttk.Scrollbar(lf, orient="horizontal", command=self.lib_tree.xview)
        self.lib_tree.configure(xscrollcommand=hvsb.set)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.lib_tree.grid(row=0, column=0, sticky="nsew")
        lvsb.grid(row=0, column=1, sticky="ns")
        hvsb.grid(row=1, column=0, sticky="ew")
        self.lib_tree.bind("<Double-1>", self.on_lib_double)
        self.lib_tree.bind("<Button-3>", self.on_lib_context)
        self.lib_tree.bind("<ButtonPress-1>", self.on_lib_drag_start)
        self.lib_tree.bind("<B1-Motion>", self.on_lib_drag_motion)
        self.lib_tree.bind("<ButtonRelease-1>", self.on_lib_drag_release)
        self.lib_tree.bind("<MouseWheel>",
                           lambda e: self.lib_tree.yview_scroll(-1 * (e.delta // 120), "units"))
        self.search_bar.bind_all("<F3>", lambda _e: self.action_find_lib())
        lbar = ttk.Frame(self.pc_right_body, padding=(0, 4)); lbar.pack(fill="x")
        ttk.Button(lbar, text="刷新曲库", command=self.action_refresh_lib).pack(side="left", padx=1)
        ttk.Button(lbar, text="下载选中", command=self.action_download).pack(side="left", padx=1)
        ttk.Button(lbar, text="改元数据", command=self.action_edit_track).pack(side="left", padx=1)
        syncbar = ttk.Frame(self.pc_right_body, padding=(0, 3)); syncbar.pack(fill="x")
        self.dirty_var = tk.StringVar(value="无暂存修改")
        ttk.Label(syncbar, textvariable=self.dirty_var, foreground="#A60").pack(side="left", padx=2)
        self.sync_btn = ttk.Button(syncbar, text="⚡ 同步暂存修改",
                                   command=self.action_sync_edits, state="disabled")
        self.sync_btn.pack(side="right", padx=2)
        self.pc_side_collapsed = {"left": False, "right": False}
        # v17：保存主套控件引用；“机器歌曲”页签保留同款窗体（副套），二者数据同步。
        self.lib_tree_main, self.pl_tree_main, self.pl_songs_main = (
            self.lib_tree, self.pl_tree, self.pl_songs)
        self._render_stage()

    # ---- v20 播放按钮自适应 / 播控模式总开关 -------------------------------
    def _layout_ctrl_buttons(self):
        """6 个播放按钮：框体够宽排 1 行 6 个，否则排 2 行各 3 个（v24）。"""
        row = getattr(self, "ctrl_btn_row", None)
        specs = getattr(self, "ctrl_buttons", None)
        if not row or not specs:
            return
        n = len(specs)
        half = (n + 1) // 2          # 6 → 3；奇数时前多后少
        # 估算一行 n 个所需最小宽度（中文按钮按像素粗估）
        need = sum(len(t) * 12 + 26 for t, _c in specs)
        try:
            w = row.winfo_width()
        except Exception:
            w = 0
        two_rows = (0 < w < need)
        mode = ("2x%d" % half) if two_rows else ("1x%d" % n)
        # 避免反复重建：布局模式没变则跳过
        if getattr(self, "_ctrl_btn_mode", None) == mode:
            return
        self._ctrl_btn_mode = mode
        for child in list(row.winfo_children()):
            child.destroy()
        self._ctrl_btn_widgets = []
        if two_rows:
            sub1 = ttk.Frame(row); sub1.pack(fill="x")
            sub2 = ttk.Frame(row); sub2.pack(fill="x")
            for i, (txt, cmd) in enumerate(specs):
                b = ttk.Button(sub1 if i < half else sub2, text=txt, command=cmd)
                b.pack(side="left", padx=2, pady=1)
                self._ctrl_btn_widgets.append(b)
        else:
            for txt, cmd in specs:
                b = ttk.Button(row, text=txt, command=cmd)
                b.pack(side="left", padx=2, pady=2)
                self._ctrl_btn_widgets.append(b)

    def action_play_skip(self, delta: int):
        """v21：播放后队首是当前曲目 → 下一曲=队列第二首(index 1)；
        上一曲从播放历史回退（无历史则到队尾）。无清单时退化为曲库相邻曲目。"""
        if getattr(self, "stage_tracks", None):
            n = len(self.stage_tracks)
            if delta > 0:
                idx = 1 if n > 1 else 0
            else:
                hist = getattr(self, "_play_history", [])
                tid = hist[-2] if len(hist) >= 2 else None
                found, hidx = self._stage_find_by_tid(tid) if tid is not None else (None, None)
                idx = hidx if found is not None else n - 1
            self.action_stage_go(idx)
            return
        # 无播控清单：按曲库顺序播放相邻曲目
        ids = [t.get("trackid") for t in getattr(self, "tracks", [])]
        if not ids:
            messagebox.showinfo("提示", "请先连接并读取曲库，或开启播控模式编排清单。")
            return
        try:
            cur = ids.index(self.play_track_id)
        except (ValueError, TypeError):
            cur = 0
        idx = (cur + delta) % len(ids)
        tid = ids[idx]
        t = self.tracks[idx]
        self.play_track_id = tid
        self.play_track_info = t
        self.play_queue_pos = None
        self._update_play_track_label()
        self.play_pos_var.set(0.0)
        self.play_elapsed_sec = 0.0
        self.on_play_scale_change(0.0)
        self._update_position_title(0.0)
        # v22：无清单的单曲播放走旧模型（播完由程序切曲），关闭观测装载标志。
        self._auto_loaded = False
        self._obs_last = None
        def fn(d):
            ok = d.play_track(tid)
            self.q.put(("play_started", tid, ok))
        self._worker(fn)

    def _set_pcmode_body_state(self, state):
        """启用/禁用播控模式下部窗体（按钮、下拉框、清单树等）。
        ttk.Treeview 没有 -state 选项，改用 selectmode=none + 交互标志拦截。"""
        body = getattr(self, "pc_mode_body", None)
        if body is None:
            return
        self._pcmode_enabled = (state == "normal")
        def walk(w):
            try:
                cls = w.winfo_class()
                if cls == "Treeview":
                    # ttk.Treeview 不支持 state；用 selectmode 禁选
                    try:
                        w.configure(selectmode=("browse" if state == "normal" else "none"))
                    except Exception:
                        pass
                elif cls in ("TButton", "TCheckbutton", "TCombobox", "TEntry"):
                    try:
                        w.configure(state=state)
                    except Exception:
                        pass
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(body)

    def action_pcmode_enable(self):
        """总开关：确认后清空机器内“选定的音乐”队列，并启用软件播控模式。"""
        if not getattr(self, "connected", False):
            messagebox.showinfo("提示", "请先连接设备，再开启播控模式。")
            return
        if not messagebox.askyesno(
                "开启播控模式",
                "此操作将清空播放器内现有的“选定的音乐”（播放队列），\n"
                "之后完全由本软件的播控清单控制播放。\n\n"
                "确认清空机器队列并开启软件控制的播控模式？"):
            return
        def fn(d):
            ok = d.clear_play_queue()
            self.q.put(("log", f"[{'OK' if ok else '!'}] 已清空机器“选定的音乐”队列。"))
            self.q.put(("pcmode_enabled", ok))
        self._worker(fn, label="正在清空机器队列并开启播控模式 ...")

    def on_stage_double_click(self, event):
        """双击播控清单某一行：立即播放该曲（GO 语义）。"""
        if not getattr(self, "_pcmode_enabled", True):
            return
        iid = self.stage_tree.identify_row(event.y)
        if not iid or not iid.isdigit():
            return
        idx = int(iid)
        if 0 <= idx < len(self.stage_tracks):
            self.stage_index = idx
            self.action_stage_go(idx)

    # ---- v18/v19/v20 选中配色 / 全局焦点 ----------------------------------
    def _configure_tree_style_map(self):
        """移除 Treeview 的 selected 内建背景映射，让行 tag 的 background 生效。

        必须在控件建好、主题确定后调用：ttk 在首个控件创建时才加载实际主题
        （如 vista）的 Treeview 默认 map，若在此之前清空会被主题默认值覆盖。
        因此 __init__ 早期调一次、_build_ui 末尾再调一次。
        """
        try:
            st = ttk.Style()
            bg = st.lookup("Treeview", "background") or "#FFFFFF"
            fg = st.lookup("Treeview", "foreground") or "#000000"
            self._tree_bg, self._tree_fg = bg, fg
            for style_name in ("Treeview", "Queue.Treeview"):
                st.map(style_name, background=[], foreground=[])
            self._focus_colors_ready = True
        except Exception:
            self._focus_colors_ready = False
            self._tree_bg, self._tree_fg = "#FFFFFF", "#000000"

    def _setup_selection_styles(self):
        """配置 Treeview 选中配色（v24 重写）：
        · 当前焦点树的选中行 → 灰蓝底白字（'hot'）；
        · 非焦点【普通树】的选中行 → 灰色底黑字（'cold'）；
        · 非焦点【播放队列 stage_tree】的选中行 → 淡黄底黑字（'warm'）。

        ★根因★：ttk 默认用主题的 SystemHighlight 作为 "selected" 状态背景，
        它会【覆盖行 tag 的 background】——这就是 v20 配色一直失效的原因。
        解决：style.map(background=[], foreground=[]) 移除内建 selected 覆盖，
        让行 tag 的 background 真正生效（已用 build/probe_focus_colors.py 验证）。
        高亮完全由行的 tag 驱动：焦点变化时给所有选中行重新打对应 tag。
        """
        self._configure_tree_style_map()
        # 全局事件：鼠标点选 / 键盘选中变化 / Tab 焦点切换，都重算高亮。
        self.root.bind_all("<ButtonPress-1>", self._on_global_tree_focus, add="+")
        self.root.bind_all("<<TreeviewSelect>>", self._on_tree_select_focus, add="+")
        self.root.bind_all("<FocusIn>", self._on_tree_focus_in, add="+")

    # ---- v27：界面皮肤（foobar2000 风格 Quick Appearance / Colors and Fonts）
    def _apply_current_skin(self, skin=None, name=None):
        """把皮肤应用到 ttk 并重打行 tag；可选即时传入皮肤用于实时预览。"""
        if skin is not None:
            self.skin = skin_cfg.normalize_skin(skin)
        if name is not None:
            self.skin_name = name
        skin_cfg.apply_skin(self.root, self.skin)
        try:
            self._configure_tree_style_map()
            self._apply_focus_highlight()
        except Exception:
            pass
        # 同步下拉框显示（不触发 ComboboxSelected 事件）
        cb = getattr(self, "skin_combo", None)
        var = getattr(self, "skin_name_var", None)
        if cb is not None and var is not None:
            if self.skin_name not in cb.cget("values"):
                cb.configure(values=skin_cfg.preset_names() + [self.skin_name])
            var.set(self.skin_name)

    def on_skin_preset_changed(self, event=None):
        """快速换肤：选中预设即应用并保存（foobar2000 Quick Setup 行为）。"""
        name = self.skin_name_var.get()
        cfg = {"version": skin_cfg.SKIN_VERSION, "name": name,
               "skin": skin_cfg.get_preset(name)}
        self._apply_current_skin(cfg["skin"], name)
        try:
            skin_cfg.save_config(cfg)
            self._log(f"[OK] 已切换皮肤：{name}")
        except Exception as exc:
            self._log(f"[!] 皮肤保存失败：{exc}")

    def action_skin_settings(self):
        """foobar2000 风格外观设置：预设 + 单项颜色/字体，实时预览，取消还原。"""
        before = {"name": self.skin_name, "skin": dict(self.skin)}
        dlg = SkinDialog(self.root, before["name"], before["skin"],
                         on_preview=lambda nm, sk: self._apply_current_skin(sk, nm))
        result = dlg.show()
        if result is None:                       # 取消 → 还原
            self._apply_current_skin(before["skin"], before["name"])
            return
        cfg = {"version": skin_cfg.SKIN_VERSION,
               "name": result["name"], "skin": result["skin"]}
        self._apply_current_skin(result["skin"], result["name"])
        try:
            skin_cfg.save_config(cfg)
            self._log(f"[OK] 外观设置已保存：{result['name']}")
        except Exception as exc:
            self._log(f"[!] 外观设置保存失败：{exc}")

    def action_skin_import(self):
        path = filedialog.askopenfilename(
            title="导入皮肤文件", filetypes=[("皮肤 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            cfg = skin_cfg.import_skin(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self._apply_current_skin(cfg["skin"], cfg["name"])
        try:
            skin_cfg.save_config(cfg)
            self._log(f"[OK] 已导入皮肤：{cfg['name']}（来自 {os.path.basename(path)}）")
        except Exception as exc:
            self._log(f"[!] 皮肤保存失败：{exc}")

    def action_skin_export(self):
        path = filedialog.asksaveasfilename(
            title="导出皮肤文件", defaultextension=".json",
            initialfile=f"NJB3皮肤_{self.skin_name}.json",
            filetypes=[("皮肤 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            skin_cfg.export_skin({"name": self.skin_name, "skin": self.skin}, path)
            self._log(f"[OK] 皮肤已导出：{path}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def action_open_layout_editor(self):
        """打开本项目的界面布局编辑器（页签顺序/分栏/列），保存后重启生效。"""
        import subprocess
        exe_dir = os.path.dirname(os.path.abspath(sys.executable)) \
            if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        # v28：打包产物的实际名称是 njb3_gui_editor.exe（见 njb3_layout_editor.spec），
        # 旧代码只找 njb3_layout_editor.exe 导致发布版永远提示"未找到"。
        # 源码副本在发布目录叫 源码_njb3_layout_editor.py，源码态则直接在 scripts/。
        candidates = [
            os.path.join(exe_dir, "njb3_gui_editor.exe"),
            os.path.join(exe_dir, "njb3_layout_editor.exe"),
            os.path.join(os.path.dirname(exe_dir), "scripts", "njb3_layout_editor.py"),
            os.path.join(exe_dir, "源码_njb3_layout_editor.py"),
            os.path.join(exe_dir, "njb3_layout_editor.py"),
        ]
        target = next((p for p in candidates if os.path.exists(p)), None)
        if not target:
            messagebox.showinfo("提示", "未找到布局编辑器文件 njb3_gui_editor.exe / 源码_njb3_layout_editor.py。")
            return
        try:
            if target.endswith(".py"):
                subprocess.Popen([sys.executable, target])
            else:
                subprocess.Popen([target])
            self._log("[OK] 已启动界面布局编辑器（保存后重启主程序生效）。")
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def _all_treeviews(self):
        """递归收集窗口内所有 ttk.Treeview（含副套/资源管理器）。"""
        out = []
        def walk(w):
            try:
                for c in w.winfo_children():
                    if c.winfo_class() == "Treeview":
                        out.append(c)
                    walk(c)
            except Exception:
                pass
        try:
            walk(self.root)
        except Exception:
            pass
        return out

    def _on_global_tree_focus(self, event):
        w = event.widget
        try:
            if w.winfo_class() == "Treeview":
                self._last_focus_tree = w
                try:
                    w.focus_set()
                except Exception:
                    pass
                self._apply_focus_highlight(w)
        except Exception:
            pass

    def _on_tree_select_focus(self, event):
        """任意树选中变化：焦点转移到它，高亮条随之移动。"""
        w = event.widget
        try:
            if w.winfo_class() == "Treeview":
                self._last_focus_tree = w
                self._apply_focus_highlight(w)
        except Exception:
            pass

    def _on_tree_focus_in(self, event):
        """Tab/键盘把焦点移到某棵树：高亮条也跟过去。"""
        w = event.widget
        try:
            if w.winfo_class() == "Treeview":
                self._last_focus_tree = w
                self._apply_focus_highlight(w)
        except Exception:
            pass

    def _apply_focus_highlight(self, focus_tree=None):
        """v24 焦点配色：遍历所有树的选中行重新打 tag。
        · 焦点树选中行 → 'hot'（灰蓝）；
        · 非焦点普通树选中行 → 'cold'（灰）；
        · 非焦点 stage_tree 选中行 → 'warm'（淡黄）。
        用 item(iid, tags=...) 重写（ttk.Treeview 无 tag_add/tag_remove）。
        """
        if focus_tree is None:
            focus_tree = getattr(self, "_last_focus_tree", None)
        FOCUS_TAGS = ("hot", "cold", "warm")
        try:
            # 先清掉上一次打过焦点 tag 的行（可能已不再选中）
            for t, iid in getattr(self, "_hot_rows", []):
                try:
                    if t.winfo_exists() and t.exists(iid):
                        old = [x for x in t.item(iid, "tags") if x not in FOCUS_TAGS]
                        t.item(iid, tags=tuple(old))
                except Exception:
                    pass
            stage = getattr(self, "stage_tree", None)
            touched = []
            # v27：行 tag 颜色取自当前皮肤（缺省仍是经典浅色三色，兼容旧行为）
            tags = skin_cfg.focus_tags(getattr(self, "skin", None))
            for tree in self._all_treeviews():
                # tag 绑定在树实例上：每棵树按需配置三种 tag（幂等）
                tree.tag_configure("hot", background=tags["hot"][0],
                                   foreground=tags["hot"][1])
                tree.tag_configure("cold", background=tags["cold"][0],
                                   foreground=tags["cold"][1])
                tree.tag_configure("warm", background=tags["warm"][0],
                                   foreground=tags["warm"][1])
                is_focus = (tree is focus_tree)
                is_stage = (tree is stage)
                for iid in tree.selection():
                    try:
                        old = [x for x in tree.item(iid, "tags") if x not in FOCUS_TAGS]
                        if is_focus:
                            newtag = "hot"
                        elif is_stage:
                            newtag = "warm"
                        else:
                            newtag = "cold"
                        tree.item(iid, tags=tuple(old + [newtag]))
                        touched.append((tree, iid))
                    except Exception:
                        pass
            self._hot_rows = touched
        except Exception:
            pass

    def _dispatch_tree_event(self, event, tree, handler):
        """让副套控件复用主套事件处理：执行期间把主引用临时指向事件来源树。"""
        attr = ("lib_tree" if tree is getattr(self, "lib_tree2", None) else
                "pl_tree" if tree is getattr(self, "pl_tree2", None) else
                "pl_songs")
        old = getattr(self, attr)
        setattr(self, attr, tree)
        try:
            return handler(event)
        finally:
            setattr(self, attr, old)

    # ---- 左右栏折叠 --------------------------------------------------
    def _pc_push_sash(self):
        """折叠/展开后把 PanedWindow 分隔条推到合适位置，实现缩进到侧边。"""
        def _apply():
            p = getattr(self, "pc_root", None)
            if p is None:
                return
            try:
                total = p.winfo_width()
                if total < 80:
                    return
                left_edge = 34 if self.pc_side_collapsed["left"] else int(total * 0.26)
                # 三栏两条分隔条：0 在左栏后，1 在右栏前
                p.sashpos(0, left_edge)
                if self.pc_side_collapsed["right"]:
                    p.sashpos(1, total - 34)
                elif not self.pc_side_collapsed["left"]:
                    p.sashpos(1, int(total * 0.78))
            except Exception:
                pass
        self.root.after(30, _apply)

    def action_pc_fold_left(self):
        if not self.pc_side_collapsed["left"]:
            self.pc_left_body.pack_forget()
            self.pc_left_title.configure(text=" 播放清单 ")
            self.pc_left_fold_btn.configure(text="»")
            self.pc_expand_left_btn.pack(side="left", padx=2)
            self.pc_side_collapsed["left"] = True
            self._pc_push_sash()

    def action_pc_unfold_left(self):
        if self.pc_side_collapsed["left"]:
            self.pc_left_body.pack(fill="both", expand=True)
            self.pc_left_title.configure(text=" 播放清单 / 列表内歌曲 ")
            self.pc_left_fold_btn.configure(text="«")
            self.pc_expand_left_btn.pack_forget()
            self.pc_side_collapsed["left"] = False
            self._pc_push_sash()

    def action_pc_fold_right(self):
        if not self.pc_side_collapsed["right"]:
            self.pc_right_body.pack_forget()
            self.pc_right_title.configure(text=" 曲库 ")
            self.pc_right_fold_btn.configure(text="«")
            self.pc_expand_right_btn.pack(side="right", padx=2)
            self.pc_side_collapsed["right"] = True
            self._pc_push_sash()

    def action_pc_unfold_right(self):
        if self.pc_side_collapsed["right"]:
            self.pc_right_body.pack(fill="both", expand=True)
            self.pc_right_title.configure(text=" 机器内歌曲 ")
            self.pc_right_fold_btn.configure(text="»")
            self.pc_expand_right_btn.pack_forget()
            self.pc_side_collapsed["right"] = False
            self._pc_push_sash()

    # -------------------------------------------------- 机器歌曲页签
    def _build_machine_tab(self, parent):
        """v17：本页与“播放控制”工作台【各自保留一套】曲库/播放清单窗体，数据同步。"""
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self.machine_paned = paned

        # ---- 左：机器内歌曲（副套 lib_tree2）----
        left = ttk.Frame(paned); paned.add(left, weight=3)
        sbar = ttk.Frame(left, padding=(0, 0, 0, 4)); sbar.pack(fill="x")
        ttk.Label(sbar, text="搜索:").pack(side="left", padx=(0, 3))
        ttk.Entry(sbar, textvariable=self.lib_search_var, width=18).pack(side="left", padx=(0, 3))
        fld = ttk.Combobox(sbar, textvariable=self.lib_search_field_var,
                           values=("标题", "艺人", "专辑", "ID", "文件名"),
                           state="readonly", width=7)
        fld.pack(side="left", padx=(0, 3))
        ttk.Button(sbar, text="定位", command=self.action_find_lib).pack(side="left", padx=(0, 3))
        ttk.Checkbutton(sbar, text="只显示匹配", variable=self.lib_search_only_var,
                        command=self._render_lib).pack(side="left")

        lf = ttk.LabelFrame(left, text=" 机器内歌曲（点表头排序 · 双击暂存 · 右键菜单） ",
                            padding=(6, 4))
        lf.pack(fill="both", expand=True)
        self.lib_tree2 = ttk.Treeview(lf, columns=self.LIB_COLS, show="headings",
                                      height=12, selectmode="extended")
        for c, h, w in zip(self.LIB_COLS, self.LIB_HEADS, self.LIB_WIDTHS):
            self.lib_tree2.heading(c, text=h, command=lambda cc=c: self.action_sort_lib(cc))
            self.lib_tree2.column(c, width=w, anchor="w")
        self.lib_tree2.column("num", width=42, anchor="center")
        self.lib_tree2.tag_configure("dirty", background="#FFF3B0")
        self.lib_tree2.tag_configure("changed", background="#FFF3B0", foreground="#D00")
        lvsb = ttk.Scrollbar(lf, orient="vertical", command=self.lib_tree2.yview)
        self.lib_tree2.configure(yscrollcommand=lvsb.set)
        hvsb = ttk.Scrollbar(lf, orient="horizontal", command=self.lib_tree2.xview)
        self.lib_tree2.configure(xscrollcommand=hvsb.set)
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)
        self.lib_tree2.grid(row=0, column=0, sticky="nsew")
        lvsb.grid(row=0, column=1, sticky="ns")
        hvsb.grid(row=1, column=0, sticky="ew")
        for seq, h in (("<Double-1>", self.on_lib_double), ("<Button-3>", self.on_lib_context),
                       ("<ButtonPress-1>", self.on_lib_drag_start),
                       ("<B1-Motion>", self.on_lib_drag_motion),
                       ("<ButtonRelease-1>", self.on_lib_drag_release)):
            self.lib_tree2.bind(seq, lambda e, h=h: self._dispatch_tree_event(e, self.lib_tree2, h))
        self.lib_tree2.bind("<MouseWheel>",
                            lambda e: self.lib_tree2.yview_scroll(-1 * (e.delta // 120), "units"))
        lbar = ttk.Frame(left, padding=(0, 5)); lbar.pack(fill="x")
        ttk.Button(lbar, text="刷新曲库", command=self.action_refresh_lib).pack(side="left", padx=2)
        ttk.Button(lbar, text="下载选中", command=self.action_download).pack(side="left", padx=2)
        ttk.Button(lbar, text="删除选中", command=self.action_delete_tracks).pack(side="left", padx=2)
        ttk.Button(lbar, text="改元数据", command=self.action_edit_track).pack(side="left", padx=2)
        syncbar = ttk.Frame(left, padding=(0, 4)); syncbar.pack(fill="x")
        ttk.Label(syncbar, textvariable=self.dirty_var, foreground="#A60").pack(side="left", padx=2)
        ttk.Button(syncbar, text="⚡ 同步暂存修改", command=self.action_sync_edits,
                   state="disabled").pack(side="right", padx=2)

        # ---- 右：播放清单 + 清单内歌曲（副套）----
        right = ttk.Frame(paned); paned.add(right, weight=2)
        pf = ttk.LabelFrame(right, text=" 播放清单 ", padding=(6, 4))
        pf.pack(fill="both", expand=True)
        self.pl_tree2 = ttk.Treeview(pf, show="tree", selectmode="browse", height=6)
        self.pl_tree2.pack(fill="both", expand=True)
        for seq, h in (("<<TreeviewSelect>>", self.on_playlist_select),
                       ("<Double-1>", self.on_playlist_double),
                       ("<Button-3>", self.on_playlist_context)):
            self.pl_tree2.bind(seq, lambda e, h=h: self._dispatch_tree_event(e, self.pl_tree2, h))
        pbar = ttk.Frame(pf, padding=(0, 4)); pbar.pack(fill="x")
        for txt, cmd in (("新建", self.action_new_playlist), ("改名", self.action_rename_playlist),
                         ("刷新", self.action_refresh_playlists), ("删除", self.action_delete_playlist)):
            ttk.Button(pbar, text=txt, command=cmd).pack(side="left", padx=2)

        pit = ttk.LabelFrame(right, text=" 清单内的歌曲（拖到播放控制页的播放队列可加入） ",
                             padding=(6, 4))
        pit.pack(fill="both", expand=True, pady=(6, 0))
        self.pl_songs2 = ttk.Treeview(pit, columns=("num", "title", "artist", "id"),
                                      show="headings", height=8)
        for c, h, w in (("num", "#", 36), ("title", "标题", 180),
                        ("artist", "艺人", 120), ("id", "ID", 60)):
            self.pl_songs2.heading(c, text=h); self.pl_songs2.column(c, width=w, anchor="w")
        plv2 = ttk.Scrollbar(pit, orient="vertical", command=self.pl_songs2.yview)
        self.pl_songs2.configure(yscrollcommand=plv2.set)
        self.pl_songs2.pack(side="left", fill="both", expand=True)
        plv2.pack(side="right", fill="y")
        for seq, h in (("<ButtonPress-1>", self.on_pl_songs_drag_start),
                       ("<B1-Motion>", self.on_pl_songs_drag_motion),
                       ("<ButtonRelease-1>", self.on_pl_songs_drag_release)):
            self.pl_songs2.bind(seq, lambda e, h=h: self._dispatch_tree_event(e, self.pl_songs2, h))

        ttk.Button(right, text="打开「播放控制」工作台 →",
                   command=lambda: self.nb.select(self.tab_playctrl)).pack(pady=6)
        self._render_lib(); self._render_playlists(); self._render_pl_songs()

    # ---------------------------------------------------------- 其他功能页签
    def _build_other_tab(self, parent):
        paned = ttk.PanedWindow(parent, orient="vertical")
        paned.pack(fill="both", expand=True)
        self.other_paned = paned

        # 上：驱动安装 + 设备信息 + 功能按钮
        top = ttk.Frame(paned)
        paned.add(top, weight=1)
        drv = ttk.LabelFrame(top, text=" 驱动安装（无需切换程序，一键完成） ", padding=(10, 8))
        drv.pack(fill="x", padx=4, pady=4)
        ttk.Label(drv, text="WinUSB 驱动装不上会导致连不上设备。点下面按钮自动提权安装：",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        btnrow = ttk.Frame(drv); btnrow.pack(fill="x", pady=(6, 0))
        self.drv_btn2 = ttk.Button(btnrow, text="⚙ 安装/修复 WinUSB 驱动", command=self.action_install_driver)
        self.drv_btn2.pack(side="left", padx=2)
        ttk.Button(btnrow, text="查看当前驱动状态", command=self.action_driver_status).pack(side="left", padx=2)
        ttk.Button(btnrow, text="驱动说明/排错", command=self.action_driver_help).pack(side="left", padx=2)

        devf = ttk.LabelFrame(top, text=" 设备信息与数据刷新（互不连带） ", padding=(10, 8))
        devf.pack(fill="x", padx=4, pady=4)
        brow = ttk.Frame(devf); brow.pack(fill="x")
        # v27★：两个刷新按钮职责分离——
        #   “刷新设备信息”只取固件/容量/电源/音量/EAX，不碰曲库与播放清单；
        #   “刷新曲库和播放清单”才读取曲库 + 清单 + 每个清单的成员数。
        self.refresh_info_btn = ttk.Button(brow, text="刷新设备信息",
                                           command=self.action_refresh_device_only)
        self.refresh_info_btn.pack(side="left", padx=2)
        self.refresh_lib_btn = ttk.Button(brow, text="刷新曲库和播放清单",
                                          command=self.action_refresh_lib_playlists)
        self.refresh_lib_btn.pack(side="left", padx=2)
        ttk.Button(brow, text="设置机主名", command=self.action_owner).pack(side="left", padx=2)

        # v27：界面外观（foobar2000 风格：快速换肤 + 外观设置 + 布局编辑）
        skinf = ttk.LabelFrame(top, text=" 界面外观 / Skin（参照 foobar2000） ", padding=(10, 8))
        skinf.pack(fill="x", padx=4, pady=4)
        srow = ttk.Frame(skinf); srow.pack(fill="x")
        ttk.Label(srow, text="当前皮肤：").pack(side="left")
        self.skin_name_var = tk.StringVar(value=self.skin_name)
        self.skin_combo = ttk.Combobox(srow, textvariable=self.skin_name_var,
                                       values=skin_cfg.preset_names(), state="readonly",
                                       width=14)
        self.skin_combo.pack(side="left", padx=(2, 6))
        self.skin_combo.bind("<<ComboboxSelected>>", self.on_skin_preset_changed)
        ttk.Button(srow, text="外观设置…", command=self.action_skin_settings).pack(side="left", padx=2)
        ttk.Button(srow, text="导入皮肤", command=self.action_skin_import).pack(side="left", padx=2)
        ttk.Button(srow, text="导出皮肤", command=self.action_skin_export).pack(side="left", padx=2)
        ttk.Button(srow, text="布局编辑器…", command=self.action_open_layout_editor).pack(side="left", padx=2)

        # v16：播放控制 / 选定音乐·舞台已迁入“播放控制”工作台；
        # 本页保留驱动、设备信息、界面外观与日志。

        # 下：日志
        bot = ttk.Frame(paned)
        paned.add(bot, weight=2)
        logf = ttk.LabelFrame(bot, text=" 日志 ", padding=(6, 4))
        logf.pack(fill="both", expand=True, padx=4, pady=4)
        self.log = tk.Text(logf, height=14, state="disabled", font=("Consolas", 9),
                           wrap="word")
        logv = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=logv.set)
        self.log.pack(side="left", fill="both", expand=True)
        logv.pack(side="right", fill="y")

    # ============================================================ 日志/事件
    def _log(self, msg: str):
        self.q.put(("log", msg))

    def _append_log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        if self.progress_win is not None and msg.strip():
            try:
                self.progress_detail_var.set(msg.strip()[:90])
            except Exception:
                pass

    def _poll(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "log":
            self._append_log(ev[1])
        elif kind == "explorer_status":
            try:
                self.explorer_status_var.set(ev[1])
            except Exception:
                pass
        elif kind == "play_pos":
            try:
                sec = float(ev[1])
                total = getattr(self, "play_duration_sec", 300)
                if sec > total:
                    self.play_duration_var.set(self._fmt_mmss(sec + 30))
                # v19★：seek 后 2.5 秒保护窗内，若设备回报的位置远小于 seek
                # 目标（陈旧值/0），丢弃它，保持界面停在用户拖到的目标位置，
                # 由 play_seeked 调度的强制同步随后刷新为真实值。
                tgt = getattr(self, "play_seek_target", 0)
                if (time.monotonic() - getattr(self, "play_seek_at", 0) < 2.5
                        and tgt >= 5 and sec < tgt * 0.5):
                    return
                shown = min(sec, total)
                # v26★：进度条与标题分模块处理——进度条遵守“无总时长不走动”，
                # 标题“当前播放位置”始终用设备真实已播秒数校正（不截断）。
                if self._current_track_identified():
                    self.play_pos_var.set(shown)
                    self.play_pos_label_var.set(self._fmt_mmss(shown))
                    self.seek_var.set(str(int(shown)))
                self.play_elapsed_sec = sec
                self._update_position_title(sec)
            except Exception:
                pass
        elif kind == "playstat2":
            try:
                self.playstat_var.set(self.playstat_var.get() + "  " + ev[1])
            except Exception:
                pass
        elif kind == "progress_show":
            self._show_progress(ev[1] if len(ev) > 1 else "正在与播放器通讯")
        elif kind == "progress_text":
            self.progress_detail_var.set(str(ev[1]))
        elif kind == "progress_hide":
            self._hide_progress()
        elif kind == "status":
            self.status_var.set(ev[1])
            self.status_lbl.configure(foreground="#060" if ev[2] else "#b00")
        elif kind == "info":
            self._set_info(ev[1])
        elif kind == "info_set":
            # 工作线程不直接碰 Tk 控件，统一由主线程设置（线程安全）
            try:
                self.info_vars[ev[1]].set(ev[2])
            except Exception:
                pass
        elif kind == "volume_probe":
            # v21：不再切换量程，仅把探测结果记录到日志备查（保留事件兼容）。
            self.volume_probe_data = ev[1]
            self._log("[*] 音量语义诊断已停用：当前固定 0-100 百分比直发。")
        elif kind == "volume_read":
            cur, lo, hi = ev[1], ev[2], ev[3]
            try:
                step = max(0, min(100, int(cur)))
                self.vol_var.set(step)
                self.vol_lbl.configure(text=str(step))
                self._log(f"[OK] 机器音量：{step}/100"
                          f"{'' if step == int(cur) else f'（机器原值 {cur}）'}")
            except Exception:
                pass
        elif kind in ("stage_mseq_updated", "stage_queue_written"):
            # v21：写入队列 = 追加队尾。把本次写入的曲目按写入先后移到清单末尾，
            # 再按位置重算机器序号，保证界面顺序 = 机器队列顺序。
            if kind == "stage_mseq_updated":
                done, rebuild = ev[1], ev[2]
                written = [tid for _i, tid in sorted(done.items())]
            else:
                written, rebuild = ev[1], ev[2]
            if rebuild:
                self._renumber_mseq(persist=False)
            elif written:
                wset = set(int(x) for x in written)
                remain, moved = [], []
                for t in self.stage_tracks:
                    try:
                        tid = int(t.get("trackid"))
                    except (TypeError, ValueError):
                        tid = None
                    (moved if tid in wset else remain).append(t)
                byid = {}
                for t in moved:
                    try:
                        byid[int(t.get("trackid"))] = t
                    except (TypeError, ValueError):
                        pass
                moved = [byid[int(tid)] for tid in written if int(tid) in byid]
                self.stage_tracks = remain + moved
                self._renumber_mseq(persist=False)
            self._render_stage()
            self._persist_stage_cache()
        elif kind == "pcmode_enabled":
            ok = ev[1]
            if ok:
                self.pcmode_var.set(True)
                self._set_pcmode_body_state("normal")
                try:
                    self.pcmode_btn.configure(text="✓ 播控模式已开启（软件控制中）",
                                              state="disabled")
                except Exception:
                    pass
                self.stage_status_var.set("播控模式已开启：编排清单后按 GO 播放")
                self._log("[OK] 播控模式已开启，下部窗体可用。")
            else:
                self._log("[!] 清空机器队列未获确认，播控模式未开启，可重试。")
        elif kind == "eax_read":
            # ev[1] 为 None 表示机器未开放 EAX：绝不勾选，并禁用总开关。
            val = ev[1]
            ok = val is not None
            try:
                self.eax_on_var.set(bool(val) if ok else False)
                state = "normal" if ok else "disabled"
                self.eax_chk.configure(state=state)
            except Exception:
                pass
            if not ok:
                self._log("[*] 机器未开放 EAX 效果，已关闭总开关（非故障）。")
        elif kind == "queue_diag":
            self.queue_diag_data = ev[1]
        elif kind == "clear_info":
            self._clear_info()
        elif kind == "sync_buttons":
            self._sync_conn_buttons()
        elif kind == "row_update":
            idx, status, pct = ev[1], ev[2], ev[3]
            if 0 <= idx < len(self.queue_rows):
                self.queue_rows[idx]["status"] = status
                self.queue_rows[idx]["progress"] = pct
                self._refresh_row(idx)
        elif kind == "lib":
            self.tracks = ev[1]
            # v11：记录设备原始顺序作为固定序号基准；清除已不存在曲目的暂存修改
            self.lib_order = [t.get("trackid") for t in self.tracks]
            valid = set(self.lib_order)
            self.pending_edits = {k: v for k, v in self.pending_edits.items() if k in valid}
            self._render_lib()
        elif kind == "playlists":
            self.playlists = ev[1]
            self._render_playlists()
            self._refresh_stage_playlists()
        elif kind == "playstat":
            try:
                self.playstat_var.set(ev[1])
            except Exception:
                pass
        elif kind == "play_state_set":
            state, name = ev[1], ev[2]
            self.play_state = state
            self.playstat_var.set(f"状态：{self._play_state_label(state)}（已发送 {name}）")
            if state == "stopped":
                self.play_sync_pending = False
                # v20：停止后进度条归零（原只停走动不归位，用户反馈“停了但不归零”）
                self._reset_play_progress()
                # v22：手动/显式停止 → 结束观测模型，避免残留快照误触队列结束边界
                self._auto_loaded = False
                self._obs_last = None
                self._was_playing = False
                self._boundary_handled = True
            else:
                # v23★：▶播放/恢复时补总时长（此路径不经过 action_stage_go）。
                # 只用本地已知曲目兜底；设备真实位置由 600ms 后的强制同步修正，
                # 避免用陈旧 queue_pos 取到错误曲目的时长。
                if getattr(self, "play_track_info", None):
                    self._sync_duration_from_track(track=self.play_track_info,
                                                   reason=f"{name}按钮")
                self.root.after(600, lambda: self._request_play_sync(force=True))
        elif kind == "play_started":
            tid, ok = ev[1], ev[2]
            self.play_state = "playing" if ok else "unknown"
            self._was_playing = bool(ok)
            self.playstat_var.set("状态：" + self._play_state_label(self.play_state))
            self.play_last_sync = time.monotonic()
            self._update_play_track_label()
            if ok:
                self.root.after(700, lambda: self._request_play_sync(force=True))
        elif kind == "play_seeked":
            sec, ok = ev[1], ev[2]
            if ok:
                self.play_state = "playing"
            self.playstat_var.set("状态：" + self._play_state_label(self.play_state)
                                  + f"（定位 {self._fmt_mmss(sec)}）")
            # v19★：seek 完成 → 主动、连续请求设备当前状态。固件 seek 是异步的，
            # 单次立即读到的 elapsed 可能还是旧值(0)，此前会打回 0 并等 30s 轮询。
            # 这里在 0.35s/1.1s/2.2s 各强制同步一次，迅速收敛到真实位置。
            if ok:
                for _delay in (350, 1100, 2200):
                    self.root.after(_delay,
                                    lambda: self._request_play_sync(force=True))
        elif kind == "play_sync_done":
            self.play_sync_pending = False
        elif kind == "play_sync":
            # v22：新签名 ("play_sync", obs, adv)。兼容 v14 及更早的旧签名
            # ("play_sync", st, elapsed, queue_pos)——老测试/脚本仍可能在用。
            if len(ev) >= 4:
                _st, _el, _qp = ev[1], ev[2], ev[3]
                obs = {"state": _st.get("state"),
                       "playing": _st.get("playing", False),
                       "paused": _st.get("paused", False),
                       "stopped": _st.get("stopped", False),
                       "elapsed": _el, "queue_pos": _qp, "raw": _st.get("raw")}
                adv = {}
            else:
                obs, adv = ev[1], ev[2]
                if not isinstance(adv, dict):
                    adv = {}
            self.play_sync_pending = False
            self.play_last_sync = time.monotonic()
            elapsed = obs.get("elapsed")
            queue_pos = obs.get("queue_pos")
            state = ("playing" if obs.get("playing") else
                     "paused" if obs.get("paused") else
                     "stopped" if obs.get("stopped") else "unknown")
            prev_state = self.play_state
            self.play_state = state
            self.playstat_var.set("状态：" + self._play_state_label(state) + "（自动同步）")

            # v22★：seek 后 2.5s 保护窗——设备回报的陈旧位置远小于目标时不采纳。
            tgt = getattr(self, "play_seek_target", 0)
            in_seek_guard = (time.monotonic() - getattr(self, "play_seek_at", 0) < 2.5
                             and tgt >= 5)
            elapsed_stale = in_seek_guard and isinstance(elapsed, int) and elapsed < tgt * 0.5

            # v22★：设备自然换曲判定（pos 前进 / elapsed 回退，且非 seek 保护窗）。
            # 仅在"已装载整队列"且当前状态在播/待机时采信，避免手动 play_track 场景误判。
            advanced = bool(adv.get("advanced")) and not in_seek_guard \
                and getattr(self, "_auto_loaded", False) and state in ("playing", "paused")
            if advanced:
                new_pos = queue_pos if isinstance(queue_pos, int) else None
                if new_pos is not None and 0 <= new_pos < len(self.stage_tracks):
                    self.play_queue_pos = new_pos
                    self.stage_index = new_pos
                    # v23：同步当前曲目对象/ID，使时长同步与其它逻辑取到同一首曲。
                    self.play_track_info = self.stage_tracks[new_pos]
                    self.play_track_id = self.stage_tracks[new_pos].get("trackid")
                # 换曲后设备 elapsed 已归零重计，进度条直接采用，不模拟推进。
                self._auto_advance_pos = (getattr(self, "_auto_advance_pos", 0) + 1)
                self._boundary_handled = False
                self.stage_status_var.set(f"设备自行前进：队列第 {new_pos + 1 if new_pos is not None else '?'} 首")
            elif queue_pos is not None and not elapsed_stale:
                # v22★：seek 保护窗内设备回报的 pos 也可能是陈旧值，一律不采纳，
                # 保持界面停在 seek 后的位置；由 play_seeked 调度的强同步随后刷新。
                self.play_queue_pos = queue_pos
                self.q.put(("playstat2", f"队列位置：{queue_pos}"))

            if elapsed is not None and not elapsed_stale:
                total = max(1, self.play_duration_sec)
                sec = min(max(0, int(elapsed)), total)
                # v26★：同上——进度条按识别结果决定是否跟随；标题始终用真实 elapsed 校正。
                if self._current_track_identified():
                    self.play_pos_var.set(float(sec))
                    self.play_pos_label_var.set(self._fmt_mmss(sec))
                    self.seek_var.set(str(int(sec)))
                self.play_elapsed_sec = max(0, int(elapsed))
                self._update_position_title(self.play_elapsed_sec)

            self._update_play_track_label()
            # v23★：观测到设备队列位置后，按当前曲目补总时长。覆盖两种场景：
            #  ① 机器内已有队列、用户只点了▶播放（未走播放选中）；
            #  ② v22 设备自行前进到下一首（pos 变化 → 取到新曲目 length）。
            self._sync_duration_from_track(reason="设备位置同步")
            self._obs_last = obs

            # v22★：设备由"播放中"变为"停止"=队列播完/被停 → 边界处理（按模式）。
            was_playing = getattr(self, "_was_playing", False) or prev_state in ("playing", "ended")
            self._was_playing = (state == "playing")
            if state == "stopped" and was_playing and not getattr(self, "_boundary_handled", False):
                self._on_queue_end()
        elif kind == "edits_synced":
            self.pending_edits.clear()
            self._render_lib()
        elif kind == "pl_tracks":
            self.pl_tracks = ev[1]
            self._render_pl_songs()
        elif kind == "pl_members":
            # 从设备读到的真实播放清单成员（保留顺序/重复，计数才准确）
            plid, members = ev[1], ev[2]
            self.pl_members[plid] = list(members)
            if self.cur_playlist == plid:
                byid = {t.get("trackid"): t for t in self.tracks}
                self.pl_tracks = [byid[m] for m in members if m in byid]
                self._render_pl_songs()
            self._render_playlists()
        elif kind == "pl_added":
            # 拖拽/右键加入成功：本地记录成员，刷新显示
            plid, ids = ev[1], ev[2]
            self.pl_members.setdefault(plid, []).extend(ids)
            if self.cur_playlist == plid:
                byid = {t.get("trackid"): t for t in self.tracks}
                self.pl_tracks = [byid[m] for m in self.pl_members[plid] if m in byid]
                self._render_pl_songs()
            self._render_playlists()
        elif kind == "stage_tracks":
            plid, ids = ev[1], ev[2]
            # plid=None 表示来源是【播放队列】（连接后回读/手动读取），不改动
            # stage_playlist_id，避免把“保存清单排序”误指向机器播放清单。
            if plid is not None:
                self.stage_playlist_id = plid
            # v21：plid=None 表示机器当前播放队列（顺序即机器顺序），
            # 直接按位置赋予机器序号；播放清单来源才用缓存序号。
            self._set_stage_from_ids(ids, machine_synced=(plid is None))
            name = (next((p.get("name") for p in self.playlists
                          if p.get("playlistid") == plid), "机器清单")
                    if plid is not None else "机器当前播放队列")
            self.stage_status_var.set(f"已载入【{name}】：{len(self.stage_tracks)} 首")
        elif kind == "busy":
            self.worker_busy = ev[1]
            self.start_btn.configure(state="disabled" if ev[1] else "normal")
            self._sync_conn_buttons()
        elif kind == "connected":
            self.connected, self.dev = ev[1], ev[2]
            if not self.connected:
                # v20：断开设备 → 停止模拟走动并把进度条归零
                self._reset_play_progress()
            self._sync_conn_buttons()
        elif kind == "ask_wakeup":
            # 设备多次探测都不在：弹窗明确告诉用户主机可能休眠/关机
            if messagebox.askyesno(
                "未检测到播放器",
                "连续多次都没有找到 NJB3 播放器。\n\n"
                "常见原因：主机曾休眠或关机、USB 线松脱。\n\n"
                "请先【开机 / 唤醒电脑】，确认播放器已通电、USB 线插牢，\n"
                "然后点【是】重新检测，点【否】稍后再试。"):
                self.action_connect()
        elif kind == "ask_occupied":
            if messagebox.askyesno(
                "设备被占用",
                "设备已找到，但接口暂时无法取得控制权。\n\n"
                "本软件已尝试自动释放自己的占用。若仍失败，请关闭所有旧的\n"
                "NJB3 工具窗口，然后点【是】重试（一般无需拔 USB 线）。"):
                self.action_connect()
        elif kind == "ask_recover_failed":
            # 自动释放+重连都失败：明确弹窗，绝不静默跳过
            if messagebox.askyesno(
                "连接恢复失败",
                "设备通信超时，已自动释放占用并重试，但仍未成功。\n\n"
                f"错误：{str(ev[1])[:100]}\n\n"
                "请先确认：播放器已开机、USB 线插牢、电脑未休眠。\n"
                "点【是】重新检测，点【否】稍后再试。"):
                self.action_connect()
        elif kind == "drv_done":
            code = ev[1]
            # v28：顶栏 drv_btn 已在 v27 删除，仅保留其他功能页的 drv_btn2
            self.drv_btn2.configure(state="normal")
            if code == 0:
                self._append_log("[OK] 驱动安装成功！正在连接设备读取机器信息…")
                # 成功后自动连接并读取机器信息，不让用户再来回切换
                self.root.after(200, self.action_connect)
            else:
                hints = {
                    2: "没有拿到管理员权限（GUI 应已自动提权，若取消请重试）。",
                    3: "没检测到 NJB3，请插好 MP3 再点安装。",
                    6: "设备在安装中消失，拔插一次 USB 后重试。",
                    7: "驱动切换未生效——请把『其他功能』页签全部日志发给开发者。",
                    8: "生成签名 CAT 失败（检查 make_cat.exe 是否存在）。",
                    9: "CAT 自检失败（digest 结构问题）。",
                    10: "CAT 预检未通过（digest/签名非法）。",
                    11: "签名证书未能写入 Root/TrustedPublisher 信任库，驱动包必被拒。",
                    12: "自检发现证书无法入库或证书链无法建立。",
                }
                self._append_log(f"[!] 驱动安装未成功（退出码 {code}）：{hints.get(code, '未知错误')}")
                self._append_log("    完整日志见 01_驱动\\安装日志.txt；也可点『查看设备状态』核对。")

    # ---- 通讯进度提示（长任务期间显示，结束自动关闭）----------------
    def _show_progress(self, text: str):
        """延迟 250ms 弹模态提示，避免快操作一闪而过。"""
        if not self.layout.get("show_progress_dialog", True):
            return
        self.progress_title_var.set(text)
        self.progress_detail_var.set("正在等待播放器响应 ...")
        if self.progress_after is not None:
            try:
                self.root.after_cancel(self.progress_after)
            except Exception:
                pass
        self.progress_after = self.root.after(250, self._create_progress_win)

    def _create_progress_win(self):
        self.progress_after = None
        if self.progress_win is not None or not self.root.winfo_exists():
            return
        win = tk.Toplevel(self.root)
        win.title("正在通讯")
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, textvariable=self.progress_title_var,
                  font=("Microsoft YaHei UI", 11, "bold")).pack(
                      fill="x", padx=18, pady=(16, 6))
        pb = ttk.Progressbar(win, mode="indeterminate", length=330)
        pb.pack(fill="x", padx=18, pady=4)
        pb.start(12)
        ttk.Label(win, textvariable=self.progress_detail_var, foreground="#555",
                  wraplength=330, justify="left").pack(fill="x", padx=18, pady=(4, 16))
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

    def _hide_progress(self):
        if self.progress_after is not None:
            try:
                self.root.after_cancel(self.progress_after)
            except Exception:
                pass
            self.progress_after = None
        win, self.progress_win = self.progress_win, None
        if win is not None:
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass

    def _set_info(self, text: str):
        """text 形如 '固件 x / 容量 y / 可用 z'"""
        parts = text.split("/")
        keys = ("fw", "cap", "free", "bat")
        for i, k in enumerate(keys):
            if i < len(parts):
                self.info_vars[k].set(parts[i].strip())

    def _clear_info(self):
        for k in ("fw", "cap", "free", "bat"):
            self.info_vars[k].set(k.upper() + "  -")

    def _sync_conn_buttons(self):
        """连接/断开按钮随状态联动：未连接才能连，已连接才能断。"""
        try:
            busy = self.worker_busy
            self.disconnect_btn.configure(
                state="normal" if (self.connected and not busy) else "disabled")
            self.connect_btn.configure(
                state="normal" if (not self.connected and not busy) else "disabled")
            vol_state = "normal" if (self.connected and not busy) else "disabled"
            for w in (self.vol_scale, self.eax_chk):
                if w is None:
                    continue
                try:
                    w.configure(state=vol_state)
                except Exception:
                    pass
        except Exception:
            pass

    # ---- 音量 / EAX ----------------------------------------------------
    def _vol_to_device(self, step):
        """v21：界面 0-100 直接作为音量寄存器值发送（v17 验证可行的方式）。"""
        return max(0, min(100, int(step)))

    def _device_to_vol(self, raw):
        """v21：机器回读值直接作为 0-100 百分比显示。"""
        return max(0, min(100, int(raw)))

    def on_volume_change(self, value):
        """拖动时只更新数字，松开鼠标才写设备。"""
        try:
            step = int(float(value))
            self.vol_lbl.configure(text=str(step))
        except Exception:
            pass

    def action_set_volume(self):
        if not getattr(self, "connected", False):
            return
        step = int(self.vol_var.get())
        def fn(d):
            ok = d.set_volume(step)
            self.q.put(("log", f"[{'OK' if ok else '!'}] 音量设为 {step}%"
                        f"{'成功' if ok else '（设备未确认）'}"))
        fn._no_popup = True
        self._worker(fn, quiet=True)

    def _read_volume(self):
        def fn(d):
            # v21：不再写 38 探测、不再做档位换算；直接读取 0-100 音量。
            try:
                cur, lo, hi = d.get_volume()
                self.q.put(("volume_read", cur, lo, hi))
            except Exception as e:
                self.q.put(("log", f"[*] 音量读取不可用：{e}"))
            try:
                on = d.get_eax_master()
                self.q.put(("eax_read", on))
            except Exception:
                pass
        fn._safe_retry = True
        self._worker(fn, label="正在读取音量/EAX ...")

    def action_eax_toggle(self):
        if not getattr(self, "connected", False):
            return
        on = bool(self.eax_on_var.get())
        def fn(d):
            ok = d.set_eax_master(on)
            self.q.put(("log", f"[{'OK' if ok else '!'}] EAX 处理器已{'开启' if on else '关闭'}"
                        f"{'' if ok else '（设备未确认）'}"))
        fn._no_popup = True
        self._worker(fn, quiet=True)

    # v19：EAX 调参面板（action_eax_panel / action_eax_read_values /
    # action_eax_apply）已取消——实测不好用且无实际意义，仅保留 EAX 勾选开关。
    # 协议层 read_eax_values/adjust_eax 仍保留：音量设置依赖 adjust_eax(0x0203)。

    # ============================================================ 传输列表
    def _refresh_row(self, idx: int):
        iid = str(idx)
        t = self.queue_rows[idx]
        tag = t["tags"]
        try:
            self.tree.item(iid, values=(
                idx + 1, t["status"], tag.get("title", ""), tag.get("artist", ""),
                tag.get("album", ""), tag.get("folder", ""),
                f'{tag.get("size",0)//1024}KB', f'{t["progress"]}%'))
        except tk.TclError:
            pass

    def _render_queue(self):
        self.tree.delete(*self.tree.get_children())
        for i in range(len(self.queue_rows)):
            self.tree.insert("", "end", iid=str(i), values=("",))
            self._refresh_row(i)

    def action_sort_queue(self, col):
        if not self.queue_rows:
            return
        asc = not (self.queue_sort[0] == col and self.queue_sort[1])
        self.queue_sort = (col, asc)
        key = {"#": lambda t: 0, "status": lambda t: t["status"],
               "title": lambda t: t["tags"].get("title", ""),
               "artist": lambda t: t["tags"].get("artist", ""),
               "album": lambda t: t["tags"].get("album", ""),
               "folder": lambda t: t["tags"].get("folder", ""),
               "size": lambda t: t["tags"].get("size", 0),
               "progress": lambda t: t["progress"]}.get(col, lambda t: 0)
        # 用原始序号作为 # 排序键
        if col == "#":
            self.queue_rows.sort(key=lambda t: self.queue_rows.index(t), reverse=not asc)
        else:
            self.queue_rows.sort(key=key, reverse=not asc)
        self._render_queue()
        self._log(f"[=] 传输列表按『{col}』{'升序' if asc else '降序'}排列。")

    def on_view_change(self, event):
        self.view_mode = "compact" if self.view_combo.get() == "紧凑" else "details"
        small = {"#": 28, "status": 50, "title": 150, "artist": 90,
                 "album": 100, "folder": 100, "size": 60, "progress": 70}
        normal = {"#": 34, "status": 62, "title": 210, "artist": 110,
                  "album": 130, "folder": 130, "size": 78, "progress": 110}
        widths = small if self.view_mode == "compact" else normal
        for c, w in widths.items():
            self.tree.column(c, width=w)

    # ---- 传输列表内部拖拽排序 ----
    def on_queue_drag_start(self, event):
        iid = self.tree.identify_row(event.y)
        self._queue_drag_from = int(iid) if iid and iid.isdigit() else None

    def on_queue_drag_motion(self, event):
        src = getattr(self, "_queue_drag_from", None)
        if src is None or self.worker_busy:
            return
        target_iid = self.tree.identify_row(event.y)
        if not target_iid or not target_iid.isdigit():
            return
        dst = int(target_iid)
        if dst == src:
            return
        self.queue_rows.insert(dst, self.queue_rows.pop(src))
        self._queue_drag_from = dst
        self._render_queue()
        self.tree.selection_set(str(dst))

    # ---- 双击编辑 ----
    def on_double_click(self, event):
        idxs = self._sel_indexes()
        if not idxs:
            return
        t = self.queue_rows[idxs[0]]["tags"]
        for k, v in self.ed.items():
            v.set(str(t.get(k, "") or ""))

    def action_apply_edit(self):
        idxs = self._sel_indexes()
        if not idxs:
            messagebox.showinfo("提示", "请先在传输列表里选中一行。")
            return
        vals = {k: v.get().strip() for k, v in self.ed.items()}
        for i in idxs:
            t = self.queue_rows[i]["tags"]
            for k, val in vals.items():
                if val:
                    t[k] = val
            self._refresh_row(i)
        self._log(f"[OK] 已把元数据应用到 {len(idxs)} 首。")

    def _sel_indexes(self):
        return [int(i) for i in self.tree.selection() if i.isdigit()]

    def action_add_files(self):
        files = filedialog.askopenfilenames(title="选择要加入传输列表的 MP3",
                                            filetypes=[("MP3 文件", "*.mp3"), ("所有文件", "*.*")])
        if not files:
            return
        added = 0
        for f in files:
            try:
                tags = read_tags(f)
            except Exception as e:
                self._log(f"[!] 跳过 {f}: {e}")
                continue
            tags["folder"] = tags.get("folder") or ""
            self.queue_rows.append({"path": f, "tags": tags, "status": STATUS_WAIT, "progress": 0})
            added += 1
        self._render_queue()
        self._log(f"[+] 已加入 {added} 首到传输列表。")

    def action_import_m3u(self):
        f = filedialog.askopenfilename(title="选择 m3u 播放清单",
                                       filetypes=[("m3u", "*.m3u *.m3u8"), ("所有", "*.*")])
        if not f:
            return
        try:
            from njb3_transfer import parse_m3u
            entries = parse_m3u(f, base=os.path.dirname(os.path.abspath(f)))
        except Exception as e:
            self._log(f"[×] 解析 m3u 失败：{e}")
            return
        n = 0
        for e in entries:
            if os.path.isfile(e):
                tags = read_tags(e); tags["folder"] = tags.get("folder") or Path(f).stem
                self.queue_rows.append({"path": e, "tags": tags, "status": STATUS_WAIT, "progress": 0})
                n += 1
        self._render_queue()
        self._log(f"[+] 从 m3u 加入 {n} 首（共 {len(entries)} 条）。")

    def action_remove_sel(self):
        idxs = sorted(self._sel_indexes(), reverse=True)
        if not idxs:
            return
        busy = [i for i in idxs if self.queue_rows[i]["status"] == STATUS_RUN]
        if busy:
            messagebox.showinfo("提示", "正在传输的条目不能移除，已跳过。")
            idxs = [i for i in idxs if i not in busy]
        for i in idxs:
            self.queue_rows.pop(i)
        self._render_queue()
        self._log(f"[-] 已移除 {len(idxs)} 首。")

    def action_clear(self):
        if self.worker_busy:
            messagebox.showinfo("提示", "传输进行中，不能清空（可停止后再清空）。")
            return
        self.queue_rows.clear()
        self._render_queue()
        self._log("[-] 已清空传输列表。")

    def action_move(self, delta):
        idxs = sorted(self._sel_indexes())
        if not idxs:
            return
        order = list(range(len(self.queue_rows)))
        for i in idxs:
            j = i + delta
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
        self.queue_rows = [self.queue_rows[k] for k in order]
        self._render_queue()

    def action_start_transfer(self):
        if not self.queue_rows:
            messagebox.showinfo("提示", "传输列表是空的，请先添加 MP3。")
            return
        pending = [i for i, row in enumerate(self.queue_rows)
                   if row["status"] in (STATUS_WAIT, STATUS_FAIL)]
        if not pending:
            messagebox.showinfo("提示", "没有待传输的条目（都是完成或传输中）。")
            return
        self.stop_flag.clear()

        def fn(d):
            self._log(f"[*] 开始传输，共 {len(pending)} 首待传。")
            ok = fail = 0
            for idx in pending:
                if self.stop_flag.is_set():
                    self._log("[!] 用户停止。")
                    break
                row = self.queue_rows[idx]
                if row["status"] == STATUS_DONE:
                    continue
                self.q.put(("row_update", idx, STATUS_RUN, 0))
                name = row["tags"].get("filename", row["path"])
                self._log(f"[→] 上传 {name} ...")
                def prog(done, total, i=idx):
                    pct = done * 100 // total if total else 0
                    self.q.put(("row_update", i, STATUS_RUN, pct))
                try:
                    tid = d.upload_track(row["path"], row["tags"], progress=prog)
                    self.q.put(("row_update", idx, STATUS_DONE, 100))
                    self._log(f"[OK] {name} 完成 (ID {tid})")
                    ok += 1
                except Exception as e:
                    self.q.put(("row_update", idx, STATUS_FAIL, 0))
                    self._log(f"[×] {name} 失败：{e}")
                    fail += 1
            self._log(f"[=] 传输结束：成功 {ok}，失败 {fail}。")

        self._worker(fn)

    def action_stop(self):
        self.stop_flag.set()
        self._log("[!] 已请求停止（当前这首传完后停止）。")

    # ============================================================ 机器曲库
    def _lib_value(self, t: dict, col: str):
        """取曲库某列的显示值。num 用设备原始序号，其余用字段。"""
        if col == "num":
            tid = t.get("trackid")
            try:
                return self.lib_order.index(tid) + 1 if tid in self.lib_order else (t.get("_seq", 0) + 1)
            except Exception:
                return t.get("_seq", 0) + 1
        if col == "id":
            return t.get("trackid", "")
        if col == "size":
            return t.get("filesize", 0) or 0
        if col == "folder":
            return t.get("folder", "")
        v = t.get(col, "")
        return v if v is not None else ""

    def _render_lib(self):
        """渲染曲库：按当前排序；主/副两套曲库表同步刷新。"""
        # 排序：num 按设备原始序号，其它按列值
        col, asc = self.lib_sort
        def keyf(t):
            v = self._lib_value(t, col)
            if isinstance(v, (int, float)):
                return (0, v)
            return (1, str(v).lower())
        rows = sorted(self.tracks, key=keyf, reverse=not asc)
        if self.lib_search_only_var.get():
            needle = self.lib_search_var.get().strip().lower()
            field = self.lib_search_field_var.get()
            if needle:
                rows = [t for t in rows if needle in str(self._search_value(t, field)).lower()]
        rendered = []
        for t in rows:
            tid = t.get("trackid")
            iid = str(tid)
            disp = dict(t)
            disp.update(self.pending_edits.get(tid, {}))
            vals = []
            for c in self.LIB_COLS:
                v = self._lib_value(disp, c)
                if c == "size":
                    v = f"{(v or 0)//1024}KB"
                elif c == "length" and isinstance(v, (int, float)) and v > 0:
                    v = f"{int(v)//60}:{int(v)%60:02d}"
                vals.append(v)
            tags = ("dirty",) if self.pending_edits.get(tid) else ()
            rendered.append((iid, vals, tags))
        for tree in (getattr(self, "lib_tree_main", None), getattr(self, "lib_tree2", None)):
            if tree is None:
                continue
            tree.delete(*tree.get_children())
            for iid, vals, tags in rendered:
                tree.insert("", "end", iid=iid, values=vals, tags=tags)
        self._update_dirty_bar()
        # v20：重建行后，若焦点在曲库树则重打高亮 tag
        f = getattr(self, "_last_focus_tree", None)
        if f in (getattr(self, "lib_tree_main", None), getattr(self, "lib_tree2", None)):
            self._apply_focus_highlight(f)

    def _update_dirty_bar(self):
        n = len(self.pending_edits)
        try:
            self.dirty_var.set(f"⚠ {n} 首有未同步修改" if n else "无暂存修改")
            self.sync_btn.configure(state="normal" if n else "disabled")
        except Exception:
            pass

    # ---- 曲库搜索定位 -------------------------------------------------
    def _search_value(self, t: dict, field: str | None = None):
        field = field or self.lib_search_field_var.get()
        mapping = {"标题": "title", "艺人": "artist", "专辑": "album",
                   "文件名": "filename", "ID": "trackid"}
        key = mapping.get(field, "title")
        return t.get(key, "")

    def action_find_lib(self):
        """按当前字段查找并定位；连续按 F3/定位找下一个。"""
        needle = self.lib_search_var.get().strip().lower()
        if not needle:
            messagebox.showinfo("搜索", "请输入要查找的内容（默认查标题）。")
            return
        rows = list(self.lib_tree.get_children())
        if not rows:
            return
        sel = self.lib_tree.selection()
        start = rows.index(sel[0]) + 1 if sel and sel[0] in rows else 0
        byid = {t.get("trackid"): t for t in self.tracks}
        for k in range(len(rows)):
            iid = rows[(start + k) % len(rows)]
            if not iid.isdigit():
                continue
            t = byid.get(int(iid))
            if t is None:
                continue
            if needle in str(self._search_value(t)).lower():
                self.lib_tree.selection_set(iid)
                self.lib_tree.focus(iid)
                self.lib_tree.see(iid)
                return
        messagebox.showinfo("搜索", f"没有找到匹配「{self.lib_search_var.get()}」的曲目。")

    # ---- 应用 GUI 编辑器保存的布局 ------------------------------------
    def apply_layout(self):
        """把 gui_layout.json 中的页签顺序/分栏/列宽等应用到当前窗口。"""
        def place(paned, frac, horizontal):
            if paned is None:
                return
            total = paned.winfo_width() if horizontal else paned.winfo_height()
            if total < 40:
                return
            pos = int(total * frac)
            try:
                paned.sashpos(0, pos)
            except Exception:
                try:
                    paned.sash(0, pos)
                except Exception:
                    pass

        sash = self.layout.get("sash", {})
        place(self.main_paned, sash.get("main_explorer", 0.32), True)
        place(getattr(self, "machine_paned", None), sash.get("machine_lib", 0.62), True)
        place(getattr(self, "other_paned", None), sash.get("other_log", 0.60), False)
        self.root.after(60, self._pc_push_sash)

        for tree, group in ((self.lib_tree, "lib_columns"), (self.tree, "queue_columns")):
            specs = self.layout.get(group, [])
            visible = [s["key"] for s in specs if s.get("visible", True)]
            if visible:
                try:
                    tree.configure(displaycolumns=visible)
                except Exception:
                    pass
            for s in specs:
                try:
                    tree.column(s["key"], width=int(s.get("width", 80)))
                except Exception:
                    pass

    def action_sort_lib(self, col):
        """点击列头：同列切换升/降序；num 始终按设备原始序号。"""
        if getattr(self, "lib_sort", None) == (col, True):
            self.lib_sort = (col, False)
        elif getattr(self, "lib_sort", None) == (col, False):
            self.lib_sort = (col, True)
        else:
            self.lib_sort = (col, True)
        self._render_lib()
        self._log(f"[=] 曲库按『{col}』{'升序' if self.lib_sort[1] else '降序'}排列。")

    def on_lib_double(self, event):
        """双击曲库单元格：暂存修改该字段（不改设备）。"""
        region = self.lib_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.lib_tree.identify_column(event.x)
        col_idx = int(col.replace("#", "")) - 1
        if col_idx < 0 or col_idx >= len(self.LIB_COLS):
            return
        key = self.LIB_COLS[col_idx]
        field = self.LIB_EDITABLE.get(key)
        if key == "id" or key == "num":
            return
        iid = self.lib_tree.identify_row(event.y)
        if not iid or not iid.isdigit():
            return
        tid = int(iid)
        t = next((x for x in self.tracks if x.get("trackid") == tid), None)
        if not t:
            return
        cur = self.pending_edits.get(tid, {}).get(field, t.get(field, "") if field else "")
        val = tk.simpledialog.askstring("暂存修改", f"修改【{key}】（暂存，点底部同步才写入播放器）：",
                                        initialvalue=str(cur or ""), parent=self.root)
        if val is None:
            return
        self.pending_edits.setdefault(tid, {})[field] = val.strip()
        self._render_lib()
        self._log(f"[+] 暂存：ID {tid} 的 {key} = {val.strip()!r}（黄底为未同步）")

    def action_sync_edits(self):
        """把暂存的字段修改批量写入播放器（走 update_string/u16_frame）。"""
        if not self.pending_edits:
            return
        items = list(self.pending_edits.items())
        total = sum(len(fields) for _, fields in items)
        if not messagebox.askyesno("确认同步", f"将 {len(items)} 首共 {total} 处暂存修改写入播放器？"):
            return
        smap = {"title": pde.F_TITLE, "artist": pde.F_ARTIST, "album": pde.F_ALBUM,
                "genre": pde.F_GENRE, "folder": pde.F_DIR}
        umap = {"year": pde.F_YEAR, "trackno": pde.F_TRACKNO, "length": pde.F_LENGTH}
        def fn(d):
            done = failn = 0
            for tid, fields in items:
                for k, val in fields.items():
                    try:
                        if k in umap:
                            d.update_u16_frame(tid, umap[k], int(val))
                        elif k in smap:
                            d.update_string_frame(tid, smap[k], str(val))
                        else:
                            continue
                        done += 1
                    except Exception as e:
                        failn += 1
                        self._log(f"[!] ID {tid} {k} 同步失败：{e}")
            self.q.put(("edits_synced", None))
            self._log(f"[OK] 同步完成：成功 {done}，失败 {failn}。")
        self._worker(fn, label=f"正在同步 {total} 处暂存修改 ...")

    def on_lib_context(self, event):
        """曲库右键菜单：删除/修改/同步/加入播放清单/增加至。"""
        iid = self.lib_tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.lib_tree.selection():
            self.lib_tree.selection_set(iid)
        m = tk.Menu(self.root, tearoff=0)
        sel = self._sel_lib()
        m.add_command(label=f"修改（{len(sel)} 首）" if len(sel) > 1 else "修改曲目信息",
                      command=self.action_edit_track)
        m.add_command(label="同步到播放器", command=self.action_sync_selected)
        m.add_separator()
        m.add_command(label="加入播放清单…", command=self.action_add_to_playlist)
        # 增加至 -> 已有播放清单子菜单
        sub = tk.Menu(m, tearoff=0)
        for pl in self.playlists:
            pid = pl.get("playlistid")
            name = pl.get("name", "未命名")
            sub.add_command(label=name, command=lambda p=pid, n=name: self.action_add_to_specific(p, n))
        m.add_cascade(label="增加至 ▶", menu=sub)
        m.add_separator()
        m.add_command(label=f"删除（{len(sel)} 首）", command=self.action_delete_tracks)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def action_sync_selected(self):
        """右键单曲同步：把该曲目的暂存修改（若有）立即写入。"""
        sel = self._sel_lib()
        if not sel:
            return
        for t in sel:
            tid = t["trackid"]
            if tid not in self.pending_edits:
                continue
            # 复用同步栏：只同步选中曲目
            keep = {tid: self.pending_edits[tid]}
            saved = self.pending_edits
            self.pending_edits = keep
            self.action_sync_edits()
            self.pending_edits = saved
            return
        messagebox.showinfo("提示", "选中曲目没有暂存修改。")

    def action_add_to_playlist(self):
        """右键加入播放清单：弹确认框后加入当前选中的列表。"""
        sel = self._sel_lib()
        if not sel or self.cur_playlist is None:
            messagebox.showinfo("提示", "请先选中曲目，并在右侧选中一个播放清单。")
            return
        pl = next((p for p in self.playlists if p.get("playlistid") == self.cur_playlist), None)
        name = pl.get("name", str(self.cur_playlist)) if pl else str(self.cur_playlist)
        if not messagebox.askyesno("确认加入", f"把 {len(sel)} 首加入播放清单【{name}】？"):
            return
        trackids = [t["trackid"] for t in sel]
        plid = self.cur_playlist
        def fn(d):
            # libnjb：加入曲目后播放清单 ID 会变，必须用设备返回的新 ID 更新缓存。
            newid = d.add_tracks_to_playlist(plid, trackids)
            self.q.put(("pl_added", newid, trackids))
            self.q.put(("playlists", d.list_playlists()))
            self._log(f"[OK] 已加入 {len(trackids)} 首到【{name}】(ID {plid} -> {newid})。")
        self._worker(fn, label=f"正在加入 {len(trackids)} 首到播放清单 ...")

    def action_add_to_specific(self, plid, name):
        """右键增加至：点选已有列表后弹确认框加入。"""
        sel = self._sel_lib()
        if not sel:
            return
        if not messagebox.askyesno("确认加入", f"把 {len(sel)} 首加入播放清单【{name}】？"):
            return
        trackids = [t["trackid"] for t in sel]
        def fn(d):
            # libnjb：加入曲目后播放清单 ID 会变，必须用设备返回的新 ID 更新缓存。
            newid = d.add_tracks_to_playlist(plid, trackids)
            self.q.put(("pl_added", newid, trackids))
            self.q.put(("playlists", d.list_playlists()))
            self._log(f"[OK] 已加入 {len(trackids)} 首到【{name}】(ID {plid} -> {newid})。")
        self._worker(fn, label=f"正在加入 {len(trackids)} 首到播放清单 ...")

    def action_refresh_lib(self):
        def fn(d):
            tracks = d.list_tracks(extended=True)
            self.q.put(("lib", tracks))
            self._log(f"[OK] 曲库已刷新，共 {len(tracks)} 首。")
        fn._safe_retry = True
        self._worker(fn, label="正在从播放器读取全部曲目 ...")

    def _sel_lib(self):
        """按 trackid 取选中曲目。v11 起曲库 iid 是 trackid（不再是列表索引），
        排序后行位置会变，必须按 ID 查找才不会选错曲目。"""
        out = []
        byid = {t.get("trackid"): t for t in self.tracks}
        for i in self.lib_tree.selection():
            if i.isdigit():
                t = byid.get(int(i))
                if t is not None:
                    out.append(t)
        return out

    def action_download(self):
        sel = self._sel_lib()
        if not sel:
            messagebox.showinfo("提示", "请先在机器歌曲里选中曲目。")
            return
        folder = filedialog.askdirectory(title="选择保存目录")
        if not folder:
            return
        def fn(d):
            for t in sel:
                name = t.get("filename") or f'{t.get("title","track")}_{t.get("trackid",0)}.mp3'
                out = os.path.join(folder, name)
                self._log(f"[←] 下载 {name} ...")
                n = d.download_track(t["trackid"], out)
                self._log(f"[OK] 已保存 {n//1024}KB -> {out}")
        self._worker(fn)

    def action_delete_tracks(self):
        sel = self._sel_lib()
        if not sel:
            messagebox.showinfo("提示", "请先在机器歌曲里选中曲目。")
            return
        names = "\n".join(f'  - {t.get("title","?")}' for t in sel[:12])
        if not messagebox.askyesno("确认删除", f"确定从设备删除这 {len(sel)} 首？\n{names}"):
            return
        ids = [t["trackid"] for t in sel]
        def fn(d):
            for tid in ids:
                d.delete_item(tid)
                self._log(f"[OK] 已删除 ID {tid}")
            self.q.put(("lib", [t for t in self.tracks if t.get("trackid") not in ids]))
        self._worker(fn)

    def action_edit_track(self):
        sel = self._sel_lib()
        if len(sel) != 1:
            messagebox.showinfo("提示", "请只选中一首曲目再改元数据。")
            return
        t = sel[0]
        vals = {}
        for key, lab in (("title", "标题"), ("artist", "艺人"), ("album", "专辑"),
                         ("genre", "流派"), ("year", "年份"), ("trackno", "曲号")):
            v = tk.simpledialog.askstring("改元数据", f"{lab}（留空跳过）：",
                                          initialvalue=str(t.get(key, "")), parent=self.root)
            if v is None:
                break
            if v.strip():
                vals[key] = v.strip()
        if not vals:
            return
        tid = t["trackid"]
        def fn(d):
            smap = {"title": pde.F_TITLE, "artist": pde.F_ARTIST,
                    "album": pde.F_ALBUM, "genre": pde.F_GENRE}
            umap = {"year": pde.F_YEAR, "trackno": pde.F_TRACKNO}
            for k, val in vals.items():
                if k in umap:
                    d.update_u16_frame(tid, umap[k], int(val))
                elif k in smap:
                    d.update_string_frame(tid, smap[k], val)
                self._log(f"[OK] ID {tid} {k}={val}")
        self._worker(fn)

    # ---- 播放清单 + 拖拽 ----
    def _render_playlists(self):
        rows = []
        for pl in self.playlists:
            pid = pl.get("playlistid", 0)
            cnt = len(self.pl_members.get(pid, ()))
            suffix = f"  [{cnt} 首]" if cnt else ""
            rows.append((f"pl::{pid}", f'{pl.get("name", "未命名")}{suffix}'))
        for tree in (getattr(self, "pl_tree_main", None), getattr(self, "pl_tree2", None)):
            if tree is None:
                continue
            tree.delete(*tree.get_children())
            for iid, text in rows:
                tree.insert("", "end", iid=iid, text=text)

    def on_playlist_select(self, event):
        sel = self.pl_tree.selection()
        if not sel or not sel[0].startswith("pl::"):
            return
        self.cur_playlist = int(sel[0][4:])
        # 先用本地已有成员立即显示（若之前读过）
        ids = list(self.pl_members.get(self.cur_playlist, []))
        byid = {t.get("trackid"): t for t in self.tracks}
        self.pl_tracks = [byid[m] for m in ids if m in byid]
        self._render_pl_songs()
        # 再从设备读取真实成员（★修复"点击看不到曲目"★）
        plid = self.cur_playlist
        def fn(d):
            members = d.get_playlist_tracks(plid)
            self.q.put(("pl_members", plid, members))
            self._log(f"[OK] 播放清单成员已从设备读取：{len(members)} 首。")
        fn._safe_retry = True
        self._worker(fn)

    def on_playlist_double(self, event):
        """双击播放清单改名。"""
        self.action_rename_playlist()

    def on_playlist_context(self, event):
        iid = self.pl_tree.identify_row(event.y)
        if not iid:
            return
        self.pl_tree.selection_set(iid)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="改名", command=self.action_rename_playlist)
        m.add_command(label="刷新成员", command=self.action_refresh_pl_members)
        m.add_separator()
        m.add_command(label="删除列表", command=self.action_delete_playlist)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def action_refresh_pl_members(self):
        if self.cur_playlist is None:
            return
        plid = self.cur_playlist
        def fn(d):
            members = d.get_playlist_tracks(plid)
            self.q.put(("pl_members", plid, members))
        fn._safe_retry = True
        self._worker(fn)

    def action_rename_playlist(self):
        """播放清单改名：先用 update_string_frame 改 F_PLNAME，再刷新列表。"""
        sel = self.pl_tree.selection()
        if not sel or not sel[0].startswith("pl::"):
            messagebox.showinfo("提示", "请先选中一个播放清单。")
            return
        pid = int(sel[0][4:])
        pl = next((p for p in self.playlists if p.get("playlistid") == pid), None)
        old = pl.get("name", "") if pl else ""
        name = tk.simpledialog.askstring("播放清单改名", "新名称：",
                                         initialvalue=old, parent=self.root)
        if not name or name.strip() == old:
            return
        new = name.strip()
        def fn(d):
            try:
                d.update_string_frame(pid, pde.F_PLNAME, new)
                self._log(f"[OK] 播放清单 ID {pid} 已改名为【{new}】。")
            except Exception as e:
                self._log(f"[!] 改名失败：{e}")
                return
            self.q.put(("playlists", d.list_playlists()))
        self._worker(fn)

    def _render_pl_songs(self):
        rows = []
        for i, t in enumerate(self.pl_tracks):
            # iid 用位置而不是 trackid：同一曲目允许在列表里出现多次。
            rows.append((f"ps::{i}", (i + 1, t.get("title", ""), t.get("artist", ""),
                                      t.get("trackid", ""))))
        for tree in (getattr(self, "pl_songs_main", None), getattr(self, "pl_songs2", None)):
            if tree is None:
                continue
            tree.delete(*tree.get_children())
            for iid, vals in rows:
                tree.insert("", "end", iid=iid, values=vals)

    def action_refresh_playlists(self):
        def fn(d):
            pls = d.list_playlists()
            self.q.put(("playlists", pls))
            self._log(f"[OK] 播放清单已刷新，共 {len(pls)} 个。")
            for pl in pls:
                pid = pl.get("playlistid")
                if pid is None:
                    continue
                try:
                    members = d.get_playlist_tracks(pid)
                    self.q.put(("pl_members", pid, members))
                except usb.core.USBError:
                    raise
                except Exception as e:
                    self._log(f"[!] 读取列表「{pl.get('name', pid)}」成员失败：{e}")
        fn._safe_retry = True
        self._worker(fn, label="正在从播放器读取播放清单和曲目数 ...")

    def action_new_playlist(self):
        name = tk.simpledialog.askstring("新建播放清单", "名称：", parent=self.root)
        if not name:
            return
        def fn(d):
            plid = d.create_playlist(name)
            if not plid:
                self._log("[!] 播放器未返回新列表 ID，新建可能未成功；请查看日志。")
                return
            self._log(f"[OK] 播放清单「{name}」已创建 (ID {plid})。")
            self.cur_playlist = plid
            self.q.put(("pl_members", plid, []))
            pls = d.list_playlists()
            self.q.put(("playlists", pls))
        self._worker(fn, label=f"正在新建播放清单「{name}」...")

    def action_delete_playlist(self):
        sel = self.pl_tree.selection()
        if not sel or not sel[0].startswith("pl::"):
            messagebox.showinfo("提示", "请先选中一个播放清单。")
            return
        pid = int(sel[0][4:])
        if not messagebox.askyesno("确认", "确定删除选中的播放清单？"):
            return
        def fn(d):
            try:
                d.delete_playlist(pid)
                self._log(f"[OK] 已删除播放清单 ID {pid}。")
            except AttributeError:
                self._log("[!] 当前协议栈不支持直接删除列表，可在播放器上删除。")
            self.q.put(("playlists", d.list_playlists()))
        self._worker(fn)

    # 机器歌曲 -> 播放清单 拖拽
    def on_lib_drag_start(self, event):
        iid = self.lib_tree.identify_row(event.y)
        self._pl_drag_ids = [int(i) for i in self.lib_tree.selection() if i.isdigit()]
        if iid and iid.isdigit() and int(iid) not in self._pl_drag_ids:
            self._pl_drag_ids = [int(iid)]

    def on_lib_drag_motion(self, event):
        if not getattr(self, "_pl_drag_ids", None):
            return
        # 简单视觉反馈：鼠标移到播放清单区域时改光标
        wx, wy = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(wx, wy)
        if widget and (widget is self.pl_tree or widget is self.pl_songs):
            self.root.configure(cursor="hand2")
        else:
            self.root.configure(cursor="")

    def on_lib_drag_release(self, event):
        self.root.configure(cursor="")
        ids = getattr(self, "_pl_drag_ids", None)
        self._pl_drag_ids = None
        if not ids or self.cur_playlist is None:
            return
        wx, wy = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(wx, wy)
        # v16：拖到中间“播放队列”列表 => 加入播放队列
        if widget == self.stage_tree:
            self._append_stage_ids(list(ids), "机器内歌曲")
            return
        in_target = widget and (widget is self.pl_tree or widget is self.pl_songs or
                                widget in self.pl_tree.winfo_children() or
                                widget in self.pl_songs.winfo_children())
        if not in_target:
            return
        # v11: ids 现在直接就是 trackid（曲库 iid = trackid），不再是列表索引
        trackids = list(ids)
        if not trackids:
            return
        plid = self.cur_playlist
        def fn(d):
            d.add_tracks_to_playlist(plid, trackids)
            self._log(f"[OK] 已把 {len(trackids)} 首加入播放清单 ID {plid}。")
            self.q.put(("pl_added", plid, trackids))
            self.q.put(("playlists", d.list_playlists()))
        self._worker(fn)

    # ---- v16：列表内歌曲 -> 播放队列 拖拽 -----------------------------
    def on_pl_songs_drag_start(self, event):
        """记录列表内歌曲被拖动的曲目（当前播放清单成员，按位置定位）。"""
        iid = self.pl_songs.identify_row(event.y)
        idxs = []
        for s in self.pl_songs.selection():
            if str(s).startswith("ps::"):
                idxs.append(int(str(s)[4:]))
        if iid and str(iid).startswith("ps::") and int(str(iid)[4:]) not in idxs:
            idxs = [int(str(iid)[4:])]
        ids = []
        for i in idxs:
            if 0 <= i < len(self.pl_tracks):
                tid = self.pl_tracks[i].get("trackid")
                try:
                    ids.append(int(tid))
                except (TypeError, ValueError):
                    pass
        self._pl_song_drag_ids = ids

    def on_pl_songs_drag_motion(self, event):
        if not getattr(self, "_pl_song_drag_ids", None):
            return
        wx, wy = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(wx, wy)
        self.root.configure(cursor="hand2" if widget == self.stage_tree else "")

    def on_pl_songs_drag_release(self, event):
        self.root.configure(cursor="")
        ids = getattr(self, "_pl_song_drag_ids", None)
        self._pl_song_drag_ids = None
        if not ids:
            return
        wx, wy = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(wx, wy)
        if widget != self.stage_tree:
            return
        self._append_stage_ids(ids, "列表内歌曲")

    # ============================================================ worker
    def _probe_presence(self) -> bool:
        """只枚举设备是否存在，不 capture、不占用接口。带多次重试。"""
        import usb.core
        for attempt in range(1, 4):
            be = pde._get_backend()
            dev = usb.core.find(backend=be, idVendor=pde.VID_CREATIVE,
                                idProduct=pde.PID_NJB3)
            if dev is not None:
                return True
            self._log(f"[!] 第 {attempt}/3 次未检测到设备，重试中 ...")
            time.sleep(0.8)
        return False

    def _connect_dev(self):
        """建立持久连接：先探测存在性（重试+弹窗），再 open/capture。"""
        if not self._probe_presence():
            self.q.put(("ask_wakeup", None))
            self.q.put(("status", "未找到设备（请检查开机/USB）", False))
            raise pde.DeviceNotFound("设备不存在")
        d = pde.NJB3Device()
        try:
            d.open()
        except pde.NeedWinUSB:
            # 首次 capture 失败：可能是本软件上次超时遗留的占用。
            # 按用户要求主动释放残留后重试一次，不再直接叫用户拔线。
            self._log("[!] 首次取得控制权失败，正在自动释放残留占用并重试 ...")
            try:
                d.recover()
            except Exception:
                pass
            time.sleep(0.4)
            d2 = pde.NJB3Device()
            try:
                d2.open()
                return d2
            except pde.NeedWinUSB:
                self.q.put(("ask_occupied", None))
                raise
        return d

    def _worker(self, fn, *args, label=None, quiet=False):
        """在同一持久连接上串行执行一个 USB 任务。

        关键修复（对比旧版）：
          1. busy 由**主线程**同步置位，杜绝"快速连点→多线程同时 capture"的竞态；
          2. 连接是持久的：capture 一次，多次任务复用，不再每次 open/close；
          3. 不再在 finally 里误关连接、误发阻塞的 ping；断开只由用户显式触发。
        """
        if self.worker_busy:
            if not quiet:
                self._log("[!] 已有任务在运行，请稍候。")
            return
        if not self.dev_lock.acquire(blocking=False):
            if not quiet:
                self._log("[!] 已有任务在运行，请稍候。")
            return
        self.q.put(("busy", True))
        if not quiet:
            self.q.put(("progress_show", label or "正在与播放器通讯"))
        self.worker_busy = True
        self._sync_conn_buttons()   # 提交时在主线程调用，安全

        # 读操作（连接信息 / 曲库 / 播放清单）超时后可安全自动重试一次；
        # 写操作（上传/下载/删除）不自动重试，避免重复写入。
        retry_ok = bool(getattr(fn, "_safe_retry", False))

        def run():
            attempt = 0
            max_attempts = 2 if retry_ok else 1
            # 连接对象用【工作线程局部变量 cur_dev】管理，不依赖 self.dev：
            # self.dev 由主线程经 queue 的 connected 事件异步更新，超时后它在
            # 工作线程里被置 None，但随后到达的事件又把它设回旧对象 —— 竞态
            # 会让重试复用一个已被 recover() 关闭句柄的设备，必然再次失败。
            cur_dev = self.dev
            try:
                while True:
                    try:
                        if cur_dev is None:
                            self._log("[*] 正在连接设备 ...")
                            cur_dev = self._connect_dev()
                            self.dev = cur_dev
                            self.q.put(("connected", True, cur_dev))
                            self.q.put(("status", "已连接", True))
                            self._log("[OK] 设备已连接。")
                        fn(cur_dev, *args)
                        break  # 成功
                    except pde.NeedWinUSB as e:
                        self.q.put(("status", "设备被占用/驱动未切换", False))
                        self._log("[×] " + str(e))
                        self._drop_connection()
                        break
                    except pde.DeviceNotFound as e:
                        self.q.put(("status", "未找到设备", False))
                        self._log("[×] " + str(e))
                        self._drop_connection()
                        break
                    except usb.core.USBError as e:
                        attempt += 1
                        self._log(f"[!] USB 错误：{e}")
                        if attempt < max_attempts:
                            self._log("[*] 正在自动释放设备占用并重新连接（无需拔 USB 线）...")
                            d = cur_dev
                            cur_dev = None          # 强制重试时新建设备对象
                            self.dev = None
                            self.q.put(("connected", False, None))
                            if d:
                                try:
                                    d.recover()
                                except Exception:
                                    pass
                            time.sleep(0.4)
                            continue  # 重建连接后重试本次读操作
                        self.q.put(("status", "USB 错误：" + str(e)[:40], False))
                        self._log("[×] 自动释放并重试后仍失败。")
                        self._drop_connection()
                        if not getattr(fn, "_no_popup", False):
                            self.q.put(("ask_recover_failed", str(e)))
                        break
                    except Exception as e:
                        self.q.put(("status", "错误：" + type(e).__name__, False))
                        self._log(f"[×] {type(e).__name__}: {e}")
                        break
            finally:
                self.worker_busy = False
                self.q.put(("busy", False))
                if not quiet:
                    self.q.put(("progress_hide", None))
                if getattr(fn, "_sync_task", False):
                    self.q.put(("play_sync_done",))
                self.dev_lock.release()

        threading.Thread(target=run, daemon=True).start()

    def _drop_connection(self):
        """出错时彻底释放占用（不弹窗），回到未连接状态。

        关键：超时后设备仍处于同步状态，普通 close() 只发一次 release，
        很可能失败或来不及，导致下次 capture 拿到 Access denied。
        必须用 recover() 读空残留 + release + reset + close 全链路清理。
        """
        d = self.dev
        self.dev = None
        self.connected = False
        if d:
            try:
                d.recover()
            except Exception:
                pass
        self.q.put(("connected", False, None))
        self.q.put(("status", "未连接", False))
        self.q.put(("clear_info", None))
        self.q.put(("sync_buttons", None))

    def action_disconnect(self):
        """显式断开：release + close，让播放器退出同步状态，可安全拔 USB 线。"""
        if self.worker_busy:
            messagebox.showinfo("提示", "当前有任务在运行，请等它结束再断开。")
            return
        self._log("[*] 正在断开设备 ...")
        d = self.dev
        self.dev = None
        self.connected = False
        if d:
            try:
                d.recover()  # 读空残留 + release + reset + close，彻底释放占用
                self._log("[OK] 已释放设备控制权，可安全拔出 USB 线。")
            except Exception as e:
                self._log(f"[!] 断开时异常：{e}")
        self.q.put(("connected", False, None))
        self.q.put(("status", "已断开 - 可安全拔出 USB 线", True))
        self._clear_info()
        self.tracks, self.playlists = [], []
        self.pl_members = {}
        self.q.put(("lib", []))
        self.q.put(("playlists", []))
        self._sync_conn_buttons()

    # ============================================================ 设备操作
    def action_refresh_device_only(self):
        """v27★：只刷新设备信息（固件/容量/电源/音量/EAX），不碰曲库与播放清单。"""
        if not getattr(self, "connected", False) or self.dev is None:
            messagebox.showinfo("提示", "请先连接设备。")
            return
        def fn(d):
            try:
                info = d.ping()
                fw = ".".join(map(str, info["fw"]))
                self.q.put(("info_set", "fw", f"固件  {fw}"))
                self._log(f"[OK] 设备信息：{info['product']} 固件 {fw}")
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读固件失败：{e}")
            try:
                u = d.get_disk_usage()
                self.q.put(("info_set", "cap", f"容量  {u['total_bytes']/1024**3:.1f}GB"))
                self.q.put(("info_set", "free", f"可用  {u['free_bytes']/1024**3:.1f}GB"))
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读容量失败：{e}")
            try:
                b = d.get_battery()
                pct = f"{b['level_percent']}%" if b.get("level_percent") is not None else "?"
                src = "外接电源" if b.get("ac_power") else "电池"
                self.q.put(("info_set", "bat", f"电源  {pct} {src}"))
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读电量失败：{e}")
            try:
                cur, lo, hi = d.get_volume()
                self.q.put(("volume_read", cur, lo, hi))
            except Exception as e:
                self._log(f"[*] 音量读取不可用：{e}")
            try:
                eax_on = d.get_eax_master()
            except Exception:
                eax_on = None
            self.q.put(("eax_read", eax_on))
            self._log("[OK] 设备信息已刷新（未读取曲库/播放清单）。")
        fn._safe_retry = True
        self._worker(fn, label="正在刷新设备信息 ...")

    def action_refresh_lib_playlists(self):
        """v27★：只刷新曲库 + 播放清单（含成员数），不读设备信息条。"""
        if not getattr(self, "connected", False) or self.dev is None:
            messagebox.showinfo("提示", "请先连接设备。")
            return
        def fn(d):
            try:
                tracks = d.list_tracks(extended=True)
                self.q.put(("lib", tracks))
                self._log(f"[OK] 曲库已刷新，共 {len(tracks)} 首。")
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读曲库失败：{e}")
            try:
                pls = d.list_playlists()
                self.q.put(("playlists", pls))
                self._log(f"[OK] 播放清单已刷新，共 {len(pls)} 个。")
                for pl in pls:
                    pid = pl.get("playlistid")
                    if pid is None:
                        continue
                    try:
                        members = d.get_playlist_tracks(pid)
                        self.q.put(("pl_members", pid, members))
                    except usb.core.USBError:
                        raise
                    except Exception as e:
                        self._log(f"[!] 读取列表「{pl.get('name', pid)}」成员失败：{e}")
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读播放清单失败：{e}")
            self._log("[OK] 曲库和播放清单已刷新（未读取设备信息）。")
        fn._safe_retry = True
        self._worker(fn, label="正在刷新曲库和播放清单 ...")

    def action_connect(self):
        def fn(d):
            # 每一项独立 try：一般失败只记录，不拖垮其它项；
            # 但 USB 超时必须上抛，交给 _worker 走"自动释放+重连+重试"，绝不静默吞掉。
            fw = "?"
            try:
                info = d.ping()
                fw = ".".join(map(str, info["fw"]))
                self._log(f"[OK] {info['product']} 固件 {fw}")
                self.q.put(("info_set", "fw", f"固件  {fw}"))
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读固件失败：{e}")
            try:
                u = d.get_disk_usage()
                self.q.put(("info_set", "cap", f"容量  {u['total_bytes']/1024**3:.1f}GB"))
                self.q.put(("info_set", "free", f"可用  {u['free_bytes']/1024**3:.1f}GB"))
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读容量失败：{e}")
            try:
                b = d.get_battery()
                pct = f"{b['level_percent']}%" if b.get("level_percent") is not None else "?"
                src = "外接电源" if b.get("ac_power") else "电池"
                self.q.put(("info_set", "bat", f"电源  {pct} {src}"))
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读电量失败：{e}")
            # 音量 + EAX 总开关：读不到不影响连接
            try:
                cur, lo, hi = d.get_volume()
                self.q.put(("volume_read", cur, lo, hi))
            except Exception as e:
                self._log(f"[*] 音量读取不可用：{e}")
            try:
                eax_on = d.get_eax_master()
            except Exception:
                eax_on = None   # 未开放：通知 UI 关闭开关，不误勾选
            self.q.put(("eax_read", eax_on))
            if not self.layout.get("auto_refresh_on_connect", True):
                self._log("[*] 布局编辑器已关闭“连接后自动刷新”，本次只读取设备信息。")
                return
            # 顺带刷新曲库/播放清单（各自独立，失败仅记录）
            try:
                tracks = d.list_tracks(extended=True)
                self.q.put(("lib", tracks))
                self._log(f"[OK] 曲库 {len(tracks)} 首。")
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读曲库失败：{e}")
            try:
                pls = d.list_playlists()
                self.q.put(("playlists", pls))
                self._log(f"[OK] 播放清单 {len(pls)} 个。")
                # 连接后自动把每个列表的成员数也从设备读出来：
                # 右侧列表的“N 首”必须来自设备，不能用本地拖拽缓存猜。
                for pl in pls:
                    pid = pl.get("playlistid")
                    if pid is None:
                        continue
                    try:
                        members = d.get_playlist_tracks(pid)
                        self.q.put(("pl_members", pid, members))
                        self._log(f"    · {pl.get('name', pid)}：{len(members)} 首")
                    except usb.core.USBError:
                        raise
                    except Exception as e:
                        self._log(f"[!] 读取列表「{pl.get('name', pid)}」成员失败：{e}")
            except usb.core.USBError:
                raise
            except Exception as e:
                self._log(f"[!] 读播放清单失败：{e}")
            # v17：连接后多策略回读【播放队列】（不是播放清单）；
            # 固件不支持时回退到本地缓存，绝不打断连接流程。
            try:
                qids = d.read_current_queue()
                if qids:
                    self._save_queue_cache(qids)
                    self.q.put(("stage_tracks", None, qids))
                    self.q.put(("queue_diag", getattr(d, "queue_diag", {})))
                    self._log(f"[OK] 已从机器回读播放队列：{len(qids)} 首。")
                else:
                    cached = self._load_queue_cache()
                    if cached:
                        self.q.put(("stage_tracks", None, cached))
                        self._log(f"[*] 队列回读为空，已恢复缓存 {len(cached)} 首。")
            except usb.core.USBError:
                raise
            except Exception as e:
                diag = getattr(d, "queue_diag", {})
                self.q.put(("queue_diag", diag))
                cached = self._load_queue_cache()
                if cached:
                    self.q.put(("stage_tracks", None, cached))
                    self._log(f"[*] 播放队列回读不可用（{str(e)[:50]}），已恢复缓存 {len(cached)} 首。")
                else:
                    self._log(f"[*] 播放队列回读不可用（固件/PDE 限制，不影响连接）：{e}")
            # 写回时也保存缓存，保证下次连接可恢复
            self._log("[*] 若队列回读始终为空，可从左右拖入编排后点『写回播放队列』。")
        fn._safe_retry = True   # 纯读操作：超时可自动释放占用后重试
        self._worker(fn, label="正在连接并刷新曲库 / 播放清单 / 播放队列 ...")

    def action_info(self):
        self.action_connect()

    def action_owner(self):
        name = tk.simpledialog.askstring("机主名", "输入机主名：", parent=self.root)
        if not name:
            return
        def fn(d):
            d.set_owner(name)
            self._log(f"[OK] 机主名已设置为 {name}")
        self._worker(fn)

    # ---------------------------------------------------------- 播控清单 / 智能队列
    def _new_stage_item(self, t):
        """把曲库曲目包装成播控清单条目（复制，避免污染曲库原始数据）。
        _seq=固定索引信号（拖动不变）；_mseq=机器序号（None=未写入机器）。"""
        item = dict(t)
        self.stage_seq_counter += 1
        item["_seq"] = self.stage_seq_counter
        item["_mseq"] = None
        return item

    def _stage_find_by_tid(self, trackid):
        """按 trackid 在播控清单里找条目（含其索引）。"""
        try:
            tid = int(trackid)
        except (TypeError, ValueError):
            return None, None
        for i, t in enumerate(self.stage_tracks):
            try:
                if int(t.get("trackid")) == tid:
                    return t, i
            except (TypeError, ValueError):
                continue
        return None, None

    def _append_stage_ids(self, ids, source="曲目"):
        """把曲目 ID 追加到播控清单视图（去重：已在清单里的不重复加入）。"""
        byid = {t.get("trackid"): t for t in self.tracks}
        added = 0
        for tid in ids:
            try:
                t = byid.get(int(tid))
            except (TypeError, ValueError):
                t = None
            if t is None:
                continue
            exist, _idx = self._stage_find_by_tid(tid)
            if exist is not None:
                continue   # 同曲已在清单，绝不重复加入
            self.stage_tracks.append(self._new_stage_item(t))
            added += 1
        if not added:
            return
        if self.stage_index is None:
            self.stage_index = 0
        self._render_stage()
        self.stage_status_var.set(
            f"已从{source}加入 {added} 首到播控清单（可点“写回队列”写入机器）")
        self._log(f"[OK] 已从{source}加入 {added} 首到播控清单。")

    def on_stage_combo_select(self, event=None):
        """下拉框选中机器清单后立即载入其曲目到播放队列视图。"""
        self.action_stage_load_playlist()

    def _refresh_stage_playlists(self):
        """把机器播放清单填进舞台面板下拉框。"""
        if not hasattr(self, "stage_pl_combo"):
            return
        old = self.stage_pl_var.get()
        names = [str(p.get("name", "")) for p in self.playlists]
        self.stage_pl_combo["values"] = names
        if old not in names:
            self.stage_pl_var.set(names[0] if names else "")

    def _set_stage_from_ids(self, ids, machine_synced=False):
        """按曲目 ID 顺序重建播控清单。
        machine_synced=True 表示来源是机器队列/已写回，逐首赋予机器序号 1..N；
        否则按缓存里的 trackid→mseq 恢复（读到的曲目若曾写入机器则保留序号）。
        """
        byid = {t.get("trackid"): t for t in self.tracks}
        cached_mseq = getattr(self, "_mseq_cache", {})   # trackid -> mseq
        self.stage_tracks = []
        for n, tid in enumerate(ids, 1):
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                continue
            t = byid.get(tid)
            if t is None:
                continue
            item = self._new_stage_item(t)
            if machine_synced:
                item["_mseq"] = n
            else:
                item["_mseq"] = cached_mseq.get(tid)
            self.stage_tracks.append(item)
        self.stage_index = 0 if self.stage_tracks else None
        self._render_stage()
        return len(self.stage_tracks)

    def _render_stage(self):
        if not hasattr(self, "stage_tree"):
            return
        self.stage_tree.delete(*self.stage_tree.get_children())
        for i, t in enumerate(self.stage_tracks):
            length = t.get("length")
            # v20：#=固定索引信号(_seq，拖动不变)；机器序号=_mseq，未写入显示 “—”
            seq = t.get("_seq", i + 1)
            mseq = t.get("_mseq")
            mseq_txt = str(mseq) if mseq else "—"
            self.stage_tree.insert("", "end", iid=str(i), values=(
                seq, mseq_txt, t.get("title", ""), t.get("artist", ""),
                self._fmt_mmss(length) if length else ""))
        if self.stage_index is not None and self.stage_tree.exists(str(self.stage_index)):
            self.stage_tree.selection_set(str(self.stage_index))
        n = len(self.stage_tracks)
        synced = sum(1 for t in self.stage_tracks if t.get("_mseq"))
        self.stage_status_var.set(f"清单 {n} 首 · 已写入机器 {synced} 首" + (
            f" · 选中第 {self.stage_index + 1} 首" if self.stage_index is not None else ""))
        # v20：重建行后重打高亮 tag（焦点若在本树）
        if getattr(self, "_last_focus_tree", None) is self.stage_tree:
            self._apply_focus_highlight(self.stage_tree)

    def on_stage_select(self, event):
        sel = self.stage_tree.selection()
        if sel and sel[0].isdigit():
            self.stage_index = int(sel[0])

    def on_stage_drag_start(self, event):
        if not getattr(self, "_pcmode_enabled", True):
            return
        iid = self.stage_tree.identify_row(event.y)
        self._stage_drag_from = int(iid) if iid and iid.isdigit() else None

    def on_stage_drag_motion(self, event):
        if not getattr(self, "_pcmode_enabled", True):
            return
        src = getattr(self, "_stage_drag_from", None)
        if src is None or self.worker_busy:
            return
        target = self.stage_tree.identify_row(event.y)
        if not target or not target.isdigit():
            return
        dst = int(target)
        if dst == src or dst >= len(self.stage_tracks):
            return
        self.stage_tracks.insert(dst, self.stage_tracks.pop(src))
        self._stage_drag_from = dst
        self.stage_index = dst
        self._render_stage()

    def on_stage_drag_release(self, event=None):
        """结束队列内拖动排序；左右侧歌曲拖入由源树各自的 release 处理。"""
        self._stage_drag_from = None

    def action_stage_load_playlist(self):
        name = self.stage_pl_var.get()
        pl = next((p for p in self.playlists if str(p.get("name", "")) == name), None)
        if not pl:
            messagebox.showinfo("提示", "请先在机器歌曲页读取播放清单，再选择一个清单。")
            return
        plid = pl.get("playlistid")
        def fn(d):
            ids = list(d.get_playlist_tracks(plid))
            self.q.put(("pl_members", plid, ids))
            self.q.put(("stage_tracks", plid, ids))
            self._log(f"[OK] 已把播放清单【{name}】载入舞台队列：{len(ids)} 首。")
        fn._safe_retry = True
        self._worker(fn, label=f"正在读取播放清单【{name}】...")

    def _queue_cache_path(self):
        """播放队列本地缓存路径（与 gui_layout.json 同目录）。"""
        try:
            return os.path.join(os.path.dirname(layout_cfg.config_path()),
                                "play_queue_cache.json")
        except Exception:
            return "play_queue_cache.json"

    def _save_queue_cache(self, ids, mseq_map=None):
        """保存播控清单到本地。v20：同时记住 trackid→机器序号映射。"""
        try:
            import json
            data = {"ids": [int(x) for x in ids],
                    "mseq": {str(k): int(v) for k, v in (mseq_map or {}).items() if v},
                    "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(self._queue_cache_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"[!] 播控清单缓存写入失败：{e}")

    def _load_queue_cache(self):
        """返回 (ids, mseq_map)，兼容旧版只有 ids 的缓存。"""
        try:
            import json
            p = self._queue_cache_path()
            if not os.path.exists(p):
                return [], {}
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            ids = [int(x) for x in d.get("ids", [])]
            mseq = {}
            for k, v in (d.get("mseq", {}) or {}).items():
                try:
                    mseq[int(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            return ids, mseq
        except Exception:
            return [], {}

    def _persist_stage_cache(self):
        """把当前播控清单及机器序号写入缓存。"""
        ids = [t.get("trackid") for t in self.stage_tracks]
        mseq = {t.get("trackid"): t.get("_mseq") for t in self.stage_tracks}
        self._save_queue_cache(ids, mseq)

    def action_stage_read_queue(self, quiet=False):
        """多策略读取机器当前播放队列；失败时回退到本地缓存并给出诊断入口。"""
        def fn(d):
            ids = []
            err = ""
            try:
                ids = d.read_current_queue()
            except Exception as exc:
                err = str(exc)
            if ids:
                self._save_queue_cache(ids)
                self.q.put(("stage_tracks", None, ids))
                self.q.put(("log", f"[OK] 已从机器回读播放队列：{len(ids)} 首。"))
            else:
                cached, cmseq = self._load_queue_cache()
                if cached:
                    self._mseq_cache = cmseq
                    self.q.put(("stage_tracks", None, cached))
                    self.q.put(("log", f"[!] 机器“選定的音樂”不开放回读（{err[:50]}）；"
                                f"已恢复上次缓存的 {len(cached)} 首，可继续编排。"))
                else:
                    self.q.put(("log", f"[!] 机器“選定的音樂”不开放回读（{err[:50]}），"
                                "且无本地缓存。可从左侧/右侧拖曲目编排后写回。"))
            diag = getattr(d, "queue_diag", None)
            if diag is not None:
                self.q.put(("queue_diag", diag))
        fn._safe_retry = True
        if quiet:
            fn._no_popup = True
            self._worker(fn, quiet=True)
        else:
            self._worker(fn, label="正在多策略读取机器播放队列 ...")

    def action_queue_diag(self):
        """弹出窗口展示各条读取命令的原始回包，便于真机排障。"""
        diag = getattr(self, "queue_diag_data", None)
        win = tk.Toplevel(self.root)
        win.title("播放队列读取诊断")
        win.geometry("720x460+240+180")
        ttk.Label(win, text="各只读命令的原始回包（供分析固件是否开放队列回读）",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=10, pady=8)
        ttk.Label(win,
                  text="【关于机器上叫“選定的音樂”的清单】v19 复核结论：它就是 Playing List\n"
                       "（易失播放队列）。已逐条核对 libnjb 2.2.7 全部 njb3_* 命令，\n"
                       "与队列相关的只有：current_track(读位置)/queue_track(写入)/\n"
                       "clear(写入)/play(写入)——没有任何『回读整条队列』的命令。\n"
                       "0x011C 是写入用帧、0x0116-0x0120 是静态寄存器、0x0119 是位置。\n"
                       "故固件不开放该清单回读，软件以本地缓存/播放清单写回为准。",
                  foreground="#a50", justify="left",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=10)
        txt = tk.Text(win, wrap="none", height=20)
        txt.pack(fill="both", expand=True, padx=10)
        sb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        if diag:
            for k, v in diag.items():
                txt.insert("end", f"[{k}] ")
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        txt.insert("end", f"{kk}={vv} ")
                else:
                    txt.insert("end", str(v))
                txt.insert("end", "\n\n")
        else:
            txt.insert("end", "尚无诊断数据。请先点『↻ 读取播放队列』。\n")
        txt.configure(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=8)

    def action_stage_add_selected(self):
        sel = self._sel_lib()
        if not sel:
            messagebox.showinfo("提示", "请先在“机器歌曲”里选中要加入的曲目。")
            return
        added = 0
        for t in sel:
            exist, _idx = self._stage_find_by_tid(t.get("trackid"))
            if exist is not None:
                continue   # 去重：同曲已在清单
            self.stage_tracks.append(self._new_stage_item(t))
            added += 1
        if self.stage_index is None and self.stage_tracks:
            self.stage_index = 0
        self._render_stage()
        self.stage_status_var.set(f"已加入 {added} 首到播控清单（可点“写回队列”写入机器）")
        self._log(f"[OK] 已把 {added} 首加入播控清单（重复曲目已跳过）。")

    def action_stage_save_queue(self, rebuild=False):
        """v21★：写回队列 = queue_track 追加到机器队尾（先写排上、后写排下）。

        · 增量：只写 _mseq 为空的曲目，按清单相对顺序逐条 queue_track；
          写完后这些曲目移到清单末尾（与机器队尾一致），整体重算机器序号。
        · rebuild=True：清空机器队列后按当前清单顺序全量写入，序号 1..N。
        已写入的曲目绝不重复 queue_track；不启动播放。
        """
        if not self.stage_tracks:
            messagebox.showinfo("提示", "播控清单是空的，没有可写回的内容。")
            return
        pending = [t for t in self.stage_tracks if not t.get("_mseq")]
        if not rebuild and not pending:
            messagebox.showinfo("提示", "清单里所有曲目都已写入机器，无需重复写入。\n"
                                "若需按当前显示顺序重排机器队列，请使用“重刷队列”。")
            return
        if not rebuild:
            if not messagebox.askyesno(
                    "写入机器队列",
                    f"把清单中尚未写入的 {len(pending)} 首按显示顺序追加到机器队列尾部？\n\n"
                    "已写入的曲目不会重复写入；不启动播放。"):
                return
        # 本次要写入的 trackid（按清单相对顺序 = 写入先后 = 机器队尾顺序）
        write_list = ([t.get("trackid") for t in self.stage_tracks]
                      if rebuild else
                      [t.get("trackid") for t in self.stage_tracks
                       if not t.get("_mseq")])

        def fn(d):
            if rebuild:
                if not d.clear_play_queue():
                    self.q.put(("log", "[!] 清空机器队列失败，重刷中止。"))
                    return
            done = []
            for tid in write_list:
                try:
                    d.queue_track(int(tid))
                except Exception as e:
                    self.q.put(("log", f"[!] 曲目 {tid} 入队失败：{e}"))
                    continue
                done.append(int(tid))
            # 回主线程：按写入顺序移到清单末尾并重算序号（控件只能主线程碰）
            self.q.put(("stage_queue_written", done, rebuild))
            self.q.put(("log", f"[OK] 已追加 {len(done)} 首到机器队列尾部"
                               f"（先写排上、后写排下）。"))

        label = "正在按清单顺序重刷机器队列 ..." if rebuild else "正在写入未同步曲目 ..."
        self._worker(fn, label=label)

    def action_stage_rebuild_queue(self):
        """重刷队列入口：按清单当前顺序全量重写并重新编号。"""
        if not messagebox.askyesno(
                "重刷机器队列",
                "将清空机器内“选定的音乐”，再按当前清单显示顺序逐首写入，\n"
                "并重新赋予机器序号 1..N。确认继续？"):
            return
        self.action_stage_save_queue(rebuild=True)

    def action_stage_clear(self):
        self.stage_tracks = []
        self.stage_index = None
        self._mseq_cache = {}
        self._render_stage()
        self._persist_stage_cache()
        def fn(d):
            ok = d.clear_play_queue()
            self._log(f"[{'OK' if ok else '!'}] 已清空机器当前播放队列及本地序号记录。")
            self.q.put(("play_state_set", "stopped", "清空队列"))
        self._worker(fn, label="正在清空机器播放队列 ...")

    def action_stage_go(self, index=None, manual=True):
        """v22★：播放 = 装载整条队列 + 播队首（不再逐曲 play_track）。

        真机已证（固件 1.40.6）：设备播完队首会自行前进到队列下一首，
        PC 只需发一次 play_track(队首) + N 次 queue_track(其余)，
        之后靠 observe_playback 观测 current_track 前进/elapsed 回退跟随即可。
        逐曲 play_track 会把下一曲 unshift 到队首、与设备自身前进竞争，
        正是队列错乱的根因，故自动切曲链绝不再调用本函数。

        本地同步：把选中曲目移到 stage_tracks[0]（保持真机 unshift 规则），
        按位置重算 _mseq，使软件清单顺序 == 机器队列顺序。
        manual=True 表示用户主动触发（GO/播放选中/上一曲下一曲），重置计数器；
        manual=False 仅由旧模型兼容路径使用。
        """
        if not self.stage_tracks:
            messagebox.showinfo("提示", "请先载入播放清单或加入曲目。")
            return
        if index is None:
            index = self.stage_index if self.stage_index is not None else 0
        n = len(self.stage_tracks)
        index = max(0, min(index, n - 1))
        t = self.stage_tracks[index]
        tid = t.get("trackid")
        # ★真机 unshift：play_track 把曲目放到队列最前，本地同步移到 0 位。
        if index > 0:
            self.stage_tracks.pop(index)
            self.stage_tracks.insert(0, t)
        self._renumber_mseq(persist=False)
        mseq = t.get("_mseq")   # 现在恒为 1
        # v23★：总时长统一从曲库元数据(0x0105 解析出的 length)同步；
        # 原此处直接 set 会绕开 _duration_synced_tid 判定。
        self._sync_duration_from_track(track=t, reason="播放选中曲目")
        self.play_track_var.set(f"当前曲目：{t.get('title', '未命名')}"
                                + (f" — {t.get('artist')}" if t.get("artist") else "")
                                + f"   [机器序号 {mseq} · 1/{n}]")
        self.play_track_info = t
        self.play_track_id = tid
        self.play_queue_pos = 0   # 播放后自己是队首（0-based）
        self._play_history.append(tid)
        if len(self._play_history) > 100:
            self._play_history = self._play_history[-100:]
        cur_mode = self._cur_mode_key() if hasattr(self, "_cur_mode_key") else "go"
        go_mode = (cur_mode == "go")
        if manual:
            self._auto_advance_pos = 0
            self._shuffle_order = None
        # 队首是当前曲目，队列第二首就是“下一首”。
        if go_mode and n > 1:
            self.stage_index = 1
        else:
            self.stage_index = 0
        # v22：标记已装载整队列 → 之后切曲只靠观测，不再主动 play_track。
        # 重置观测快照与边界标志，避免沿用上一次播放的陈旧 pos。
        self._auto_loaded = True
        self._obs_last = None
        self._was_playing = True
        self._boundary_handled = False
        self._render_stage()
        self._persist_stage_cache()

        # v22：一次性装载整条队列。队首要先 play_track（真实播放），
        # 其余按清单顺序 queue_track 追加到队尾，设备随后自行顺序播放。
        rest_ids = [x.get("trackid") for x in self.stage_tracks[1:]]
        def fn(d):
            ok = d.play_track(tid)
            queued = 0
            for qtid in rest_ids:
                if qtid is None:
                    continue
                try:
                    d.queue_track(qtid)
                    queued += 1
                except Exception:
                    pass
            self.q.put(("play_started", tid, ok))
            self.q.put(("log", f"[{'OK' if ok else '!'}] 播放【{t.get('title','?')}】"
                               f"（ID {tid}），并装载后续 {queued} 首；"
                               "之后由设备自行顺序播放，程序只观测不打断。"))
        self._worker(fn, label=f"正在装载队列并播放【{t.get('title','?')}】 ...")

    def _assign_mseq(self, t, index=None, reason=""):
        """给清单条目分配机器序号（=当前最大序号+1），并同步本地缓存。"""
        if t.get("_mseq"):
            return t.get("_mseq")
        used = [x.get("_mseq") or 0 for x in self.stage_tracks]
        seq = max(used + [0]) + 1
        t["_mseq"] = seq
        try:
            tid = int(t.get("trackid"))
            self._mseq_cache[tid] = seq
        except (TypeError, ValueError):
            pass
        self._render_stage()
        self._persist_stage_cache()
        if reason:
            self._log(f"[*] 【{t.get('title','?')}】{reason}，赋予机器序号 {seq}。")
        return seq

    def _renumber_mseq(self, persist=True):
        """v21★：把 stage_tracks 顺序当作机器队列顺序，按位置重算 _mseq=1..N。

        真机规则（2026-11-20 用户实测）：
          · queue_track（写回队列）= 追加到队尾，先写排上、后写排下；
          · play_track（播放选中）= 插到队首，后播放的反而排在前（LIFO/unshift）。
        所以软件清单必须与机器队列同序，_mseq 是“当前队列位置”而不是
        “写入时分配的固定序号”。播放/写入后立即调用本方法同步。
        """
        for i, t in enumerate(self.stage_tracks):
            seq = i + 1
            t["_mseq"] = seq
            try:
                tid = int(t.get("trackid"))
                self._mseq_cache[tid] = seq
            except (TypeError, ValueError):
                pass
        if persist:
            self._persist_stage_cache()

    def _cur_mode_key(self):
        """返回当前播放模式键（go / single / ...）。"""
        name = self.play_mode_var.get()
        return next((key for label, key in getattr(self, "PLAY_MODES", ())
                     if label == name), "go")

    def on_play_mode_change(self, event=None):
        """选择即应用：立即切换程序控制模式，无需再点按钮。"""
        self.action_apply_play_mode()

    def action_apply_play_mode(self):
        """选择即生效。PDE 无机器模式命令，全部由本程序编排队列+游标实现。"""
        key = self._cur_mode_key()
        label = next((l for l, k in self.PLAY_MODES if k == key), key)
        # 随机模式：生成/重置遍历顺序；其余模式回到队列原序
        self._shuffle_order = None
        if key in ("shuffle", "shuffle_repeat") and self.stage_tracks:
            import random as _random
            self._shuffle_order = list(range(len(self.stage_tracks)))
            _random.shuffle(self._shuffle_order)
        tip = {
            "go": "按 GO（或 F5）逐曲播放：每次播放焦点曲目后游标自动指向下一曲。",
            "single": "单曲：播放当前曲目，播完停止，游标不动。",
            "repeat_one": "单曲重复：反复播放当前曲目，直到手动停止。",
            "normal": "顺序：按队列顺序逐曲播放，放完最后一首停止。",
            "repeat_all": "循环：按队列顺序循环播放。",
            "shuffle": "随机：按随机顺序播放一遍后停止。",
            "shuffle_repeat": "随机重复：按随机顺序循环播放。",
        }.get(key, "")
        self.stage_status_var.set(f"播放模式：{label}")
        self._log(f"[OK] 播放模式已切换为【{label}】。{tip}")
        self.q.put(("play_mode_ok", key, True))

    def action_stage_step(self, delta: int):
        if not self.stage_tracks:
            return
        base = self.stage_index if self.stage_index is not None else 0
        idx = (base + delta) % len(self.stage_tracks)
        if delta == 0:
            idx = base
        self.action_stage_go(idx)

    def action_stage_toggle(self):
        cmd = pde.NJB3Device.PLAY_RESUME if getattr(self, "play_state", "") == "paused" \
            else pde.NJB3Device.PLAY_PAUSE
        name = "恢复" if cmd == pde.NJB3Device.PLAY_RESUME else "暂停"
        def fn(d):
            ok = d.play_control(cmd)
            self.q.put(("play_state_set", "playing" if name == "恢复" else "paused", name))
            self._log(f"[{'OK' if ok else '!'}] 舞台模式：{name}")
        self._worker(fn)

    def action_stage_stop(self):
        def fn(d):
            ok = d.play_control(pde.NJB3Device.PLAY_STOP)
            self.q.put(("play_state_set", "stopped", "停止"))
            self._log(f"[{'OK' if ok else '!'}] 舞台模式：停止")
        self._worker(fn)

    def action_stage_save(self):
        if not self.stage_tracks:
            messagebox.showinfo("提示", "舞台队列为空，没有可保存的内容。")
            return
        if self.stage_playlist_id is None:
            messagebox.showinfo("提示", "请先“载入清单”，保存排序会写回该机器清单。")
            return
        pl = next((p for p in self.playlists
                   if p.get("playlistid") == self.stage_playlist_id), None)
        name = pl.get("name") if pl else "未命名清单"
        if not messagebox.askyesno(
                "确认写回", f"将当前顺序写回机器清单【{name}】？\n\n"
                "series-3 协议没有原地排序命令，设备会新建同名清单并删除旧清单，"
                "因此清单 ID 会变化。"):
            return
        oldid = self.stage_playlist_id
        ids = [t.get("trackid") for t in self.stage_tracks]
        def fn(d):
            newid = d.replace_playlist(oldid, name, ids)
            self.stage_playlist_id = newid
            self.q.put(("playlists", d.list_playlists()))
            self._log(f"[OK] 清单【{name}】已按当前顺序写回（ID {oldid} -> {newid}）。")
        self._worker(fn, label=f"正在写回清单【{name}】...")

    def action_stage_keymap(self):
        dlg = KeyMapDialog(self.root, self.stage_keys, self._apply_stage_keys)
        self._capturing_key = True
        dlg.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_capturing_key", False), dlg.destroy()))
        dlg.grab_set()

    def _apply_stage_keys(self, keys: dict):
        self.stage_keys = dict(keys)
        self._capturing_key = False
        self._log("[OK] 舞台按键映射已更新，保存在 stage_keys.json。")

    def _sync_stage_mode(self):
        on = self.stage_mode_var.get()
        self.stage_status_var.set("舞台按键已启用" if on else "舞台按键已关闭")

    def _on_stage_key(self, event):
        if not getattr(self, "stage_mode_var", None) or not self.stage_mode_var.get():
            return
        if getattr(self, "_capturing_key", False):
            return
        try:
            cls = event.widget.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Entry", "TEntry", "Text", "TText", "TCombobox", "Spinbox", "TSpinbox"):
            return
        k = (event.keysym or "").lower()
        action = next((a for a, v in self.stage_keys.items() if str(v).lower() == k), None)
        if not action:
            return
        if self.worker_busy and action != "select_up" and action != "select_down":
            self._log("[!] 正在通讯，按键已忽略。")
            return "break"
        if action == "play":
            self.action_stage_go()
        elif action == "go":
            self.action_stage_go()
        elif action == "pause_resume":
            self.action_stage_toggle()
        elif action == "stop":
            self.action_stage_stop()
        elif action == "prev":
            self.action_stage_step(-1)
        elif action == "next":
            self.action_stage_step(1)
        elif action == "select_up":
            self.action_stage_step_selection(-1)
        elif action == "select_down":
            self.action_stage_step_selection(1)
        return "break"

    def action_stage_step_selection(self, delta: int):
        if not self.stage_tracks:
            return
        idx = self.stage_index if self.stage_index is not None else 0
        idx = (idx + delta) % len(self.stage_tracks)
        self.stage_index = idx
        self._render_stage()

    # ---------------------------------------------------------- 播放控制
    def action_play_ctrl(self, cmd: int):
        name = {0: "播放", 1: "停止", 2: "暂停", 3: "恢复"}.get(cmd, str(cmd))
        def fn(d):
            ok = d.play_control(cmd)
            self._log(f"[{'OK' if ok else '!'}] 播放控制【{name}】{'成功' if ok else '设备未确认'}")
            state = {0: "playing", 1: "stopped", 2: "paused", 3: "playing"}.get(cmd, "unknown")
            self.q.put(("play_state_set", state, name))
        self._worker(fn)

    def _request_play_sync(self, force=False):
        """只读观测一次设备播放状态/位置/进度，并判定设备是否自然换曲。

        v22：用协议层 observe_playback()（状态+队列位置+已播秒数一次读全），
        与上一次快照做 advance_detected 判定。GUI 靠 pos 前进/elapsed 回退
        感知设备自己切换了曲目，绝不逐曲 play_track 打断队列。
        自动调度，不弹进度框、不打断用户操作。
        """
        if not getattr(self, "connected", False):
            self.play_sync_pending = False
            return
        if getattr(self, "play_sync_pending", False) and not force:
            return
        if getattr(self, "worker_busy", False):
            # 用户操作占用连接时跳过这一轮，下一秒再试，绝不排队堆积。
            self.play_sync_pending = False
            return
        self.play_sync_pending = True
        prev = getattr(self, "_obs_last", None)
        def fn(d):
            obs = d.observe_playback()
            try:
                adv = pde.NJB3Device.advance_detected(prev or {}, obs)
            except Exception:
                adv = {"advanced": False, "pos_advanced": False,
                       "pos_wrapped": False, "elapsed_reset": False}
            self.q.put(("play_sync", obs, adv))
        fn._sync_task = True
        fn._no_popup = True
        self._worker(fn, quiet=True)
        self.play_last_sync = time.monotonic()

    def _auto_advance_on_end(self):
        """v21：自动播放切换（适配 play_track unshift 语义）。

        play_track 每次把曲目放到机器队首，本地清单同步后：
          · 刚播完的曲目在 index 0；
          · stage_index 指向下一首要播的（正常为 0，GO 为 1）。
        用 _auto_advance_pos 统计本轮已自动播放首数，normal/shuffle 播完
        一轮即停止；repeat_all/shuffle_repeat 循环。手动播放会重置计数器。
        """
        key = self._cur_mode_key() if hasattr(self, "_cur_mode_key") else "go"
        self.play_state = "stopped"
        n = len(getattr(self, "stage_tracks", []))
        if not n or key in ("go", "single"):
            self.playstat_var.set("状态：" + self._play_state_label("stopped") + "（播完停止）")
            return
        cur = self.stage_index if self.stage_index is not None else 0
        cur = max(0, min(cur, n - 1))
        if key == "repeat_one":
            self.play_pos_var.set(0.0)
            self.play_elapsed_sec = 0.0
            self.on_play_scale_change(0.0)
            self._update_position_title(0.0)
            self.action_stage_go(0, manual=False)
            self.stage_status_var.set("单曲重复：重播当前曲目")
            return
        self._auto_advance_pos = getattr(self, "_auto_advance_pos", 0) + 1
        if key in ("normal", "shuffle") and self._auto_advance_pos >= n:
            self.playstat_var.set("状态：播放完毕")
            self.stage_status_var.set("本轮播放已结束")
            return
        if key in ("shuffle", "shuffle_repeat"):
            order = self._shuffle_order or list(range(n))
            try:
                at = order.index(cur)
            except ValueError:
                at = -1
            nxt_at = at + 1
            if nxt_at >= len(order):
                import random as _random
                _random.shuffle(order)
                self._shuffle_order = order
                nxt_at = 0
            nxt = order[nxt_at]
        else:
            nxt = cur + 1
            if nxt >= n:
                nxt = 0   # repeat_all 循环回队首
        self.action_stage_go(nxt, manual=False)

    def _on_queue_end(self):
        """v22★：播放队列播完（state playing→stopped）时的边界处理。

        新观测模型下，队首由设备自行前进到队尾；到队尾后设备停止，
        本方法按播放模式决定是否"重装整队列重播"：
          · repeat_all / shuffle_repeat：重装并重播；
          · repeat_one：只重播当前曲目（重载单曲队列）；
          · go / single / normal / shuffle / 其它：停止，进度归零。
        防重入：_boundary_handled 置位，直到下一次换曲/播放才复位。
        """
        if getattr(self, "_boundary_handled", False):
            return
        self._boundary_handled = True
        self._was_playing = False
        key = self._cur_mode_key() if hasattr(self, "_cur_mode_key") else "go"
        n = len(getattr(self, "stage_tracks", []))
        if not n:
            self._reset_play_progress()
            return

        if key in ("repeat_all", "shuffle_repeat"):
            # 循环：重排（随机模式洗牌）后从队首重装整条队列
            if key == "shuffle_repeat":
                import random as _random
                order = list(range(n))
                _random.shuffle(order)
                self.stage_tracks = [self.stage_tracks[i] for i in order]
                self._renumber_mseq(persist=False)
            self.stage_status_var.set("循环：队列播完，从头重播")
            self.stage_index = 0
            self._render_stage()
            self.action_stage_go(0, manual=False)
            return

        if key == "repeat_one":
            cur = self.play_queue_pos if isinstance(self.play_queue_pos, int) else 0
            cur = max(0, min(cur, n - 1))
            self.stage_status_var.set("单曲重复：重播当前曲目")
            self.action_stage_go(cur, manual=False)
            return

        # go / single / normal / shuffle：正常播完，停止并归零
        self.play_state = "stopped"
        self.playstat_var.set("状态：" + self._play_state_label("stopped") + "（队列播放完毕）")
        self.stage_status_var.set("队列播放完毕（已停止）")
        self._reset_play_progress()
        self._auto_loaded = False
        self._log("[OK] 队列已顺序播放完毕，按当前模式停止。")

    def _reset_play_progress(self):
        """v20：把播放进度条与时间显示归零（停止/断开时调用）。v26：含独立标题秒数。"""
        try:
            self.play_state = "stopped"
            self.play_pos_var.set(0.0)
            self.play_elapsed_sec = 0.0
            self.seek_var.set("0")
            self.on_play_scale_change(0.0)
            self.play_pos_label_var.set("00:00")
            self._update_position_title(0.0)
            self.play_seek_target = 0
            self.play_sync_pending = False
        except Exception:
            pass

    def _tick_playback(self):
        """v22 观测驱动：本地进度只用于平滑显示，到头【不】主动切曲，
        而是靠高频轮询 observe_playback 感知设备自然前进/停止。

        · 已装载整队列(_auto_loaded=True)：设备自己按队列播，本函数绝不 play_track。
          - 进度到头 → 标记 ended，等观测到 pos 前进/elapsed 回退自动切下一曲；
          - 观测到 stopped → 边界处理（循环模式重装队列，其余停止）。
        · 未装载(旧手动 play_track 场景, _auto_loaded=False)：保留 v21 ended 主动切曲，
          兼容"单曲播放选中逐曲 GO"等旧行为。
        """
        try:
            if not getattr(self, "root", None) or not self.root.winfo_exists():
                return
            # v20：设备断开后绝不模拟走动（原逻辑只看 play_state，断线仍继续走）
            if not getattr(self, "connected", False):
                if self.play_state in ("playing", "paused", "ended"):
                    self._reset_play_progress()
                return
            if self.play_state in ("playing", "paused"):
                # v26★：标题“当前播放位置”是【独立模块】——播放中该秒数始终每秒 +1，
                # 不管软件是否识别到曲目；30 秒一次的机器同步再把它校正回真实 elapsed。
                if self.play_state == "playing":
                    self.play_elapsed_sec = float(getattr(self, "play_elapsed_sec", 0.0)) + 1
                    self._update_position_title()
                # v25 保留：进度条【另一个模块】——仅在识别到曲目（有真实总时长）时
                # 走动；点“▶播放”直接播机器中队列、软件识别不到曲目时，不随时间空走。
                if self.play_state == "playing" and self._current_track_identified():
                    total = max(1, self.play_duration_sec)
                    # 进度条跟随独立秒数（标题为真相源），到头封顶并标记结束。
                    nxt = min(float(self.play_elapsed_sec), total)
                    self.play_pos_var.set(nxt)
                    self.play_pos_label_var.set(self._fmt_mmss(nxt))
                    self.seek_var.set(str(int(nxt)))
                    if self.play_elapsed_sec >= total:
                        self.play_state = "ended"
            if self.play_state == "ended":
                if not getattr(self, "_auto_loaded", False):
                    # 旧模型：手动逐曲播放，播完由程序主动切下一曲（v21 兼容）
                    self._auto_advance_on_end()
                # 新模型：什么都不做——等设备观测确认换曲；仅加速轮询。
                self._request_play_sync(force=True)
            elif self.play_state in ("playing", "paused"):
                # v25★：识别到曲目且已装载整队列 → 2s 高频观测自然前进；
                # 识别不到曲目（直接播机器中队列）→ 固定 30s 同步一次机器，
                # 软件不做模拟走动，仅刷新标题里的真实播放位置。
                identified = self._current_track_identified()
                interval = 2 if (identified
                                 and getattr(self, "_auto_loaded", False)) else 30
                if time.monotonic() - self.play_last_sync >= interval:
                    self._request_play_sync()
        finally:
            try:
                self.root.after(1000, self._tick_playback)
            except Exception:
                pass

    def _play_state_label(self, state: str) -> str:
        return {"playing": "播放中", "paused": "暂停",
                "stopped": "停止", "ended": "播放结束"}.get(state, "未知")

    def _current_track_identified(self) -> bool:
        """v25：软件是否识别到当前播放曲目（能对应到本地曲库对象）。

        真机事实：设备播放接口只回【队列位置 + 已播秒数】，不回 trackid/曲名/总时长。
        因此：
          · 走“播放选中曲目 / 装载队列 GO”时，软件手里有曲目对象 → 识别得到；
          · 直接点上方“▶播放”播机器已有队列、且未读回队列时，软件手里没有曲目
            对象 → 识别不到。此时进度条不做每秒模拟走动（没有真实总时长，模拟
            无意义），只靠 30 秒设备同步把标题“当前播放位置”刷新成真实 elapsed。
        """
        if getattr(self, "play_track_info", None):
            return True
        tid = getattr(self, "play_track_id", None)
        if tid is not None and any(
                t.get("trackid") == tid for t in (getattr(self, "tracks", []) or [])):
            return True
        pos = getattr(self, "play_queue_pos", None)
        stage = getattr(self, "stage_tracks", []) or []
        if isinstance(pos, int) and 0 <= pos < len(stage):
            return True
        return False

    def _sync_duration_from_track(self, track=None, reason=""):
        """把当前播放曲目的【曲库时长】同步到“总时长”（v23）。

        数据来源说明（用户疑问“是不是没用 0x0105 帧”）：
          `0x0105 / F_LENGTH` 是【曲库元数据帧】，只在 `list_tracks()` 全量
          扫描曲目数据库时解析，缓存进 track['length']。PDE 的播放接口
          （play/elapsed/current_track/seek）【没有】“读当前曲目总时长”命令，
          libnjb 2.2.7 全部播放 API 里也不存在。因此播放时进度条总时长只能
          从本地曲库缓存按当前曲目取——本方法就是这条链路的唯一入口。
        调用时机：播放选中/GO/切歌、▶播放/恢复、查询状态、设备位置同步。
        不覆盖用户在同一曲目内手动输入的总时长（以 _duration_synced_tid 判定）。
        """
        t = track
        if t is None:
            # 优先按设备回报的队列位置取（能跟随机器内已有队列的播放）
            pos = getattr(self, "play_queue_pos", None)
            stage = getattr(self, "stage_tracks", []) or []
            if isinstance(pos, int) and 0 <= pos < len(stage):
                t = stage[pos]
            if t is None:
                t = getattr(self, "play_track_info", None)
            if t is None:
                tid = getattr(self, "play_track_id", None)
                if tid is not None:
                    t = next((x for x in (getattr(self, "tracks", []) or [])
                              if x.get("trackid") == tid), None)
        if t is None:
            return False
        try:
            length = int(t.get("length") or 0)
        except (TypeError, ValueError):
            return False
        if length <= 0:
            return False
        tid = t.get("trackid")
        # 同一曲目不重复覆盖（用户可能已手动修正总时长）
        if getattr(self, "_duration_synced_tid", None) == tid:
            return False
        self.play_duration_var.set(self._fmt_mmss(length))
        self._duration_synced_tid = tid
        tail = f"（{reason}）" if reason else ""
        self._log(f"[OK] 总时长已按曲库时长 0x0105 同步：{self._fmt_mmss(length)}{tail}")
        return True

    def _update_play_track_label(self):
        """曲目名取播放队列视图；队列号一律显示『当前号/总数』（用户指定格式）。"""
        pos = self.play_queue_pos
        total = len(getattr(self, "stage_tracks", []))
        info = getattr(self, "play_track_info", None)
        # 队列号直接用设备返回的位置值（用户确认它就是机器当前播放队列的序号）
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
            text += f"   [{cur}/{total}]" if total else f"   [{cur}/?]"
        self.play_track_var.set(text)

    @staticmethod
    def _fmt_mmss(sec: float) -> str:
        sec = max(0, int(sec))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    @staticmethod
    def _parse_mmss(text: str) -> int:
        text = str(text).strip()
        try:
            if ":" in text:
                m, s = text.split(":", 1)
                return max(0, int(float(m)) * 60 + int(float(s or 0)))
            return max(0, int(float(text)))
        except Exception:
            return 300

    def _tick_positions(self, total: int) -> list[int]:
        """按总长度决定主刻度：短曲均分 4 段；长曲取 5 整倍数分钟且最多 5 个主刻度。"""
        if total <= 900:                       # ≤15 分钟：保持均分（4 段）
            return [int(total * i / 4) for i in range(5)]
        # >15 分钟：步长取 5 分钟的整数倍（5/10/15/20…分钟），使主刻度数 ≤5
        step = 300
        while total / step > 4 and step < total:   # 最多 4 个间隔 => 5 个主刻度
            step += 300
        positions = list(range(0, total + 1, step))
        if positions[-1] < total:
            positions.append(total)            # 末尾补到总长（终点标注）
        return positions[:5]

    # 进度条滑块宽度（像素）：滑块“中心”只在 [half, w-half] 之间移动，
    # 时间刻度必须按滑块中心的可达范围映射，否则 00:00/末尾刻度会偏出。
    SCALE_THUMB_HALF = 8

    def _tick_x(self, sec, total, width):
        """把时间 sec 映射为进度条上滑块中心的 x 坐标（考虑滑块宽度）。"""
        half = self.SCALE_THUMB_HALF
        usable = max(1, width - 2 * half)
        return half + usable * (min(max(sec, 0), total) / max(1, total))

    def _update_play_ticks(self):
        """总时长变化时重算滑动条上限、主刻度标签与刻度线。"""
        total = self._parse_mmss(self.play_duration_var.get())
        self.play_duration_sec = total
        try:
            self.play_scale.configure(to=max(1, total))
        except Exception:
            pass
        positions = self._tick_positions(total)
        # 标签始终是 5 个控件（兼容布局与旧测试）：多余的显示为空
        labels = getattr(self, "play_tick_labels", [])
        shown = positions if len(positions) <= len(labels) else positions[:len(labels)]
        for i, lab in enumerate(labels):
            lab.configure(text=self._fmt_mmss(shown[i]) if i < len(shown) else "")
        self._layout_play_ruler()
        self.on_play_scale_change(self.play_pos_var.get())

    def _layout_play_ruler(self):
        """按真实时间位置同时摆放刻度线与标签，二者坐标一致、文字中点对准刻度。"""
        cv = getattr(self, "play_tick_canvas", None)
        if cv is None:
            return
        try:
            w = cv.winfo_width()
            if w < 20:
                cv.after(80, self._layout_play_ruler)
                return
            total = max(1, self.play_duration_sec)
            positions = self._tick_positions(total)
            h = cv.winfo_height()
            cv.delete("all")
            # 先画一条淡淡的时间轴，再画刻度竖线
            cv.create_line(self.SCALE_THUMB_HALF, h - 1, w - self.SCALE_THUMB_HALF,
                           h - 1, fill="#bbb", width=1)
            labels = getattr(self, "play_tick_labels", [])
            npos = len(positions)
            for i, sec in enumerate(positions):
                x = int(self._tick_x(sec, total, w))
                cv.create_line(x, 2, x, max(5, h - 2), fill="#888", width=1)
                if i < len(labels):
                    # v19★：首尾标签贴容器边对齐，避免 "00:00" 的第一个 0 /
                    # 末尾标签最后一位超出左右边界被裁剪；中间标签仍按刻度居中。
                    if i == 0:
                        labels[i].place(x=0, y=h + 1, anchor="nw")
                    elif i == npos - 1:
                        labels[i].place(x=w, y=h + 1, anchor="ne")
                    else:
                        labels[i].place(x=x, y=h + 1, anchor="n")
                    labels[i].lift()
            # 多余的标签隐藏
            for j in range(len(positions), len(labels)):
                labels[j].place_forget()
        except Exception:
            pass

    def _draw_tick_marks(self, positions, total):
        """兼容旧调用：按给定 positions 重画刻度线。"""
        return self._layout_play_ruler()

    def _update_position_title(self, sec=None):
        """v26★：播放控制框体标题“当前播放位置 XX:XX”的【独立显示模块】。

        与进度条 play_pos_var 解耦：无论软件是否识别到当前曲目，标题都显示
        本地估算的已播秒数（play_elapsed_sec），每秒走动，并由 30 秒一次的
        机器同步校正。进度条是否走动另由 _current_track_identified() 决定。
        """
        if sec is None:
            sec = float(getattr(self, "play_elapsed_sec", 0.0))
        try:
            cf = getattr(self, "ctrl_frame", None)
            if cf is not None:
                cf.configure(text=f" 播放控制（通过 USB 控制播放器）    当前播放位置 {self._fmt_mmss(sec)} ")
        except Exception:
            pass

    def on_play_scale_change(self, value):
        """拖动/回读时更新进度条文字与 seek 目标（v26：不再负责标题）。"""
        try:
            sec = float(value)
        except Exception:
            sec = 0.0
        self.play_pos_label_var.set(self._fmt_mmss(sec))
        self.seek_var.set(str(int(sec)))

    def _selected_track_anywhere(self):
        """v18：按“最后焦点”优先，汇总三个栏目的选中曲目。

        返回 (track, 来源说明)。识别：机器内歌曲(lib_tree/lib_tree2)、
        清单内歌曲(pl_songs/pl_songs2)、播放队列(stage_tree)。谁最后获得焦点
        就优先用谁，避免同时选中时判断歧义。
        """
        byid = {t.get("trackid"): t for t in getattr(self, "tracks", [])}
        focus = getattr(self, "_last_focus_tree", None)
        # 各栏目的“选中项 -> track”提取器
        def from_lib(widget):
            return [byid[int(i)] for i in widget.selection()
                    if i.isdigit() and int(i) in byid]
        def from_pl(widget):
            out = []
            for iid in widget.selection():
                if iid.startswith("ps::"):
                    idx = int(iid[4:])
                    if 0 <= idx < len(getattr(self, "pl_tracks", [])):
                        out.append(self.pl_tracks[idx])
                elif iid.isdigit() and int(iid) in byid:
                    out.append(byid[int(iid)])
            return out
        def from_stage(widget):
            out = []
            for iid in widget.selection():
                if iid.isdigit():
                    idx = int(iid)
                    if 0 <= idx < len(getattr(self, "stage_tracks", [])):
                        out.append(self.stage_tracks[idx])
            return out
        # (控件对象, 来源名, 提取器)：用控件对象身份比较焦点，不能用名字字符串
        sources = [
            (self.lib_tree, "lib_tree", self._sel_lib),
            (getattr(self, "lib_tree2", None), "lib_tree2",
             lambda: from_lib(self.lib_tree2)),
            (self.pl_songs, "pl_songs", lambda: from_pl(self.pl_songs)),
            (getattr(self, "pl_songs2", None), "pl_songs2",
             lambda: from_pl(self.pl_songs2)),
            (self.stage_tree, "stage_tree", lambda: from_stage(self.stage_tree)),
        ]
        # 焦点栏目优先（控件对象身份相等）
        ordered = [s for s in sources if s[0] is not None and s[0] is focus] + \
                  [s for s in sources if s[0] is None or s[0] is not focus]
        for _widget, name, getter in ordered:
            try:
                sel = getter()
            except Exception:
                sel = []
            if sel:
                return sel[0], name
        return None, None

    def action_play_selected(self):
        """v21★：真机规则——播放选中曲目会插到机器队列最前面（后播在前）。

        用户 2026-11-20 实测：先播放 A，再写入 B/C，再播放 D，再写入 E，
        再播放 F → 机器队列顺序 FDABCE。即：
          · 写入队列 = 追加队尾（先写排上、后写排下）；
          · 播放选中 = unshift 队首（后播放的反而在前）。

        所以从任意栏选中曲目点“播放选中曲目”时：
          · 曲目已在清单 → 移到清单首位（不新增重复条目）；
          · 曲目不在清单 → 插到清单首位（保持唯一身份，不追加到末尾）；
        随后按新位置重算机器序号并调用 play_track。
        """
        t, source = self._selected_track_anywhere()
        if not t:
            messagebox.showinfo("提示",
                                "请在【机器内歌曲 / 清单内歌曲 / 播控清单】任一栏选中一首曲目。")
            return
        tid = t.get("trackid")
        exist, idx = self._stage_find_by_tid(tid)
        if exist is not None:
            if idx != 0:
                self.stage_tracks.pop(idx)
                self.stage_tracks.insert(0, exist)
            item = exist
            self._log(f"[*] 【{item.get('title','?')}】已在清单，按真机规则移到队列首位。")
        else:
            item = self._new_stage_item(t)
            self.stage_tracks.insert(0, item)
            self._log(f"[OK] 【{item.get('title','?')}】不在清单，按真机规则插到队列首位。")
        self.stage_index = 0
        # 先按新顺序重算序号（即使下游 action_stage_go 被测试替身替换也保持一致）
        self._renumber_mseq(persist=False)
        self.action_stage_go(0)

    def action_play_status(self):
        """查询状态：强制观测一次，并补齐当前曲目总时长（v23/v24）。"""
        ok = self._sync_duration_from_track(reason="查询状态")
        if not ok and getattr(self, "play_queue_pos", None) is None:
            self._log("[*] 总时长暂未知：设备未回报队列位置，且尚无选中的播放曲目。")
        # v24★：固件只回“队列第几首(current_track)+已播秒数(elapsed)”，
        # 不回曲目 ID/曲名/总时长；曲名与时长靠本地清单按位置匹配。
        self._log("[*] 说明：固件仅回队列位置与已播秒数；曲名/总时长由本地清单按位置"
                  "匹配。若机器内队列被手动改过，请先点【读回队列】再查询。")
        self._request_play_sync(force=True)

    def action_seek(self):
        try:
            sec = int(float(self.play_pos_var.get()))
        except Exception:
            messagebox.showinfo("提示", "请先拖动播放位置滑动条。")
            return
        # ★设备 seek 越界会停播并显示“播放错误”，这里必须按真实时长夹取。
        total = max(1, self.play_duration_sec)
        if sec >= total:
            sec = max(0, total - 1)
        sec = max(0, sec)
        self.seek_var.set(str(sec))
        self.play_pos_label_var.set(self._fmt_mmss(sec))
        self.play_pos_var.set(float(sec))
        self.play_elapsed_sec = float(sec)
        self._update_position_title(sec)
        if sec == 0:
            self._log("[*] 位置在曲目开头，无需 seek。")
            return
        # v19★：记录 seek 目标与时刻；界面先停在目标位置，等主动同步刷新。
        self.play_seek_target = sec
        self.play_seek_at = time.monotonic()
        def fn(d):
            # 停止状态直接 seek 会被设备判为非法；先恢复/重播当前曲目。
            if self.play_state in ("stopped", "unknown", "ended") and self.play_track_id:
                try:
                    d.play_track(self.play_track_id)
                except Exception:
                    pass
            ok = d.seek(sec * 1000)
            self._log(f"[{'OK' if ok else '!'}] 已定位到 {sec} 秒")
            # ★不再在此立即 put play_pos(get_elapsed_time())：seek 异步，此刻
            # 读到的是旧值会把进度条打回 0。改由 play_seeked 事件调度多次强同步。
            self.q.put(("play_seeked", sec, ok))
        self._worker(fn)

    # ============================================================ 驱动
    def action_install_driver(self):
        if not is_admin():
            if messagebox.askyesno(
                "安装驱动需要管理员权限",
                "安装 WinUSB 驱动需要管理员权限。\n\n"
                "点『是』后会弹出 Windows 提权确认，随后自动完成驱动安装，\n"
                "完成后回到本界面。期间无需切换到其它程序。\n\n是否继续？"):
                if relaunch_admin("--install-driver"):
                    self.root.quit()
                else:
                    messagebox.showerror("提权失败", "用户取消了提权，或未成功启动管理员实例。")
            return
        # v28：顶栏 drv_btn 已在 v27 删除，仅保留其他功能页的 drv_btn2
        self.drv_btn2.configure(state="disabled")
        self.nb.select(self.tab_other)   # 切到日志页看进度
        self._log("[驱动] 开始安装 WinUSB 驱动（管理员）...")
        root = package_root()
        ps1 = root / "01_驱动" / "njb3_driver.ps1"
        if not ps1.exists():
            ps1 = root / "njb3_driver.ps1"
        if not ps1.exists():
            self._log("[×] 找不到驱动脚本 njb3_driver.ps1，请检查发布包完整性。")
            self.drv_btn2.configure(state="normal")
            return
        def run():
            try:
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", str(ps1), "-Install"]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1)
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    if line and "__NJB3_DRIVER_DONE__" not in line:
                        self.q.put(("log", "  " + line))
                code = proc.wait()
                self.q.put(("drv_done", code))
            except Exception as e:
                self.q.put(("log", f"[×] 驱动安装异常：{e}"))
                self.q.put(("drv_done", -1))
        threading.Thread(target=run, daemon=True).start()

    def action_driver_status(self):
        root = package_root()
        ps1 = root / "01_驱动" / "njb3_driver.ps1"
        if not ps1.exists():
            ps1 = root / "njb3_driver.ps1"
        if not ps1.exists():
            self._log("[×] 找不到驱动脚本。")
            return
        def run():
            try:
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", str(ps1), "-Status"]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1)
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    if line:
                        self.q.put(("log", "  " + line))
                proc.wait()
            except Exception as e:
                self.q.put(("log", f"[×] {e}"))
        threading.Thread(target=run, daemon=True).start()

    def action_driver_help(self):
        messagebox.showinfo(
            "驱动说明",
            "点『安装/修复 WinUSB 驱动』即可在本程序内完成驱动切换：\n"
            "  - 自动申请管理员权限（弹一次 UAC）\n"
            "  - 用 Zadig 同款机制创建合规签名 CAT + 安装 WinUSB\n"
            "  - 完成后自动回到本界面，点『连接设备』\n\n"
            "旧版用未签名的 INF，被 Windows 拒绝（0xE000022F/0x80092009），已修复。")


def _probe_mode(root, app):
    """隐藏自检：确认打包后窗口与控件真实构建成功，写报告后退出。"""
    import json
    info = None
    try:
        out = app_dir() / "_gui_probe.json"
        info = {
            "ok": True,
            "title": root.title(),
            "tabs": [app.nb.tab(t, "text") for t in app.nb.tabs()],
            "queue_cols": list(app.tree["columns"]),
            "lib_cols": list(app.lib_tree["columns"]),
            "lib_displaycols": list(app.lib_tree["displaycolumns"]),
            "native_explorer": False,   # 已按用户要求回退为自建资源管理器
            "native_embedded": False,
            "file_tree_columns": list(app.file_tree["columns"]),
            "has_file_drag": bool(app.file_tree.bind("<ButtonRelease-1>")),
            "has_play_scale": hasattr(app, "play_scale"),
            "has_play_track_info": hasattr(app, "play_track_var"),
            "has_stage_queue": hasattr(app, "stage_tree"),
            "has_stage_keymap": hasattr(app, "stage_keys") and bool(app.stage_keys),
            "stage_default_keys": dict(DEFAULT_STAGE_KEYS),
            "has_playlist_reorder": hasattr(app, "action_stage_save"),
            # v16 播放控制工作台
            "has_playctrl_tab": hasattr(app, "tab_playctrl"),
            "playctrl_three_columns": (hasattr(app, "pc_left") and hasattr(app, "pc_mid")
                                       and hasattr(app, "pc_right")),
            "playctrl_side_fold": (hasattr(app, "action_pc_fold_left")
                                   and hasattr(app, "action_pc_fold_right")
                                   and hasattr(app, "action_pc_unfold_left")
                                   and hasattr(app, "action_pc_unfold_right")),
            "pl_songs_drag_to_queue": bool(app.pl_songs.bind("<ButtonRelease-1>")),
            "lib_drag_to_queue": bool(app.lib_tree.bind("<ButtonRelease-1>")),
            "stage_queue_reorder": bool(app.stage_tree.bind("<B1-Motion>")),
            "has_write_queue": hasattr(app, "action_stage_save_queue"),
            # v17 新增
            "terminology_playlist": "播放清单",     # 原播放列表 -> 播放清单
            "terminology_queue": "播放队列",        # 原设备队列 -> 播放队列
            "machine_tab_dup_lib": hasattr(app, "lib_tree2"),
            "machine_tab_dup_playlist": (hasattr(app, "pl_tree2")
                                         and hasattr(app, "pl_songs2")),
            "queue_multi_read": (hasattr(app, "action_stage_read_queue")
                                 and hasattr(app, "action_queue_diag")),
            "queue_cache": callable(getattr(app, "_load_queue_cache", None)),
            "has_volume": hasattr(app, "vol_scale"),
            "has_eax_panel": False,   # v19：EAX 调参面板已取消，仅保留勾选开关
            "play_modes": [k for _l, k in getattr(app, "PLAY_MODES", ())],
            "tick_canvas": hasattr(app, "play_tick_canvas"),
            # v18 新增 / v21 改为 0-100
            "volume_0_25": False,
            "volume_0_100": (str(app.vol_scale.cget("from")) == "0"
                             and str(app.vol_scale.cget("to")) == "100"),
            "has_volume_probe": hasattr(app, "action_volume_probe"),
            "eax_guard": hasattr(app, "eax_chk") and not hasattr(app, "action_eax_panel"),
            "playmode_select_apply": callable(getattr(app, "on_play_mode_change", None))
                                     and callable(getattr(app, "_auto_advance_on_end", None)),
            "ruler_by_time": (hasattr(app, "_tick_x") and hasattr(app, "_layout_play_ruler")
                              and getattr(app, "SCALE_THUMB_HALF", 0) > 0),
            "play_sel_multi": callable(getattr(app, "_selected_track_anywhere", None)),
            "focus_colors": (hasattr(app, "_setup_selection_styles")
                             and getattr(app, "stage_tree", None) is not None
                             and str(app.stage_tree.cget("style")) == "Queue.Treeview"),
            "explorer_fold": (hasattr(app, "explorer_fold_btn")
                              and callable(getattr(app, "action_explorer_fold", None))),
            "queue_index_format": "cur/All",
            "play_auto_sync": hasattr(app, "_request_play_sync"),
            "play_tick_running": hasattr(app, "_tick_playback"),
            # v22 观测驱动播放模型
            "observe_model": (callable(getattr(app, "_on_queue_end", None))
                              and callable(getattr(app, "_request_play_sync", None))),
            "advance_detect": callable(getattr(pde.NJB3Device, "advance_detected", None))
                              and callable(getattr(pde.NJB3Device, "observe_playback", None)),
            # v23：播放时总时长从曲库元数据(0x0105→track['length'])同步
            "duration_from_cache": (callable(getattr(app, "_sync_duration_from_track", None))
                                    and hasattr(app, "_duration_synced_tid")),
            "has_audio_button": hasattr(app, "action_audio_capability"),
            "folder_roots": ([app.folder_tree.item(i, "text")
                              for i in app.folder_tree.get_children()]
                             if getattr(app, "folder_tree", None) is not None else []),
            "has_sync_btn": bool(str(app.sync_btn["text"]).strip()),
            "has_play_ctrl": hasattr(app, "playstat_var"),
            "explorer_always_left": (getattr(app, "folder_tree", None) is not None
                                     or getattr(app, "native_explorer", None) is not None),
            "has_driver_btn": bool(str(app.drv_btn2["text"]).strip()),
            "has_disconnect_btn": bool(str(app.disconnect_btn["text"]).strip()),
            "has_connect_btn": bool(str(app.connect_btn["text"]).strip()),
            "disconnect_initial_state": str(app.disconnect_btn["state"]),
            "has_lib_search": bool(app.search_bar.winfo_ismapped()),
            "has_lib_scrollbar": bool(app.lib_tree.winfo_exists()),
            "has_progress_dialog": bool(app.layout.get("show_progress_dialog")),
            "has_log": app.log.winfo_exists() == 1,
            # v20 新增
            "pcmode_switch": (hasattr(app, "pcmode_btn") and hasattr(app, "pc_mode_body")
                              and hasattr(app, "action_pcmode_enable")),
            "pcmode_default_off": (not bool(getattr(app, "pcmode_var", None)
                                            and app.pcmode_var.get())),
            "machine_seq_col": ("mseq" in app.stage_tree["columns"]
                                if hasattr(app, "stage_tree") else False),
            "ctrl_8_buttons": len(getattr(app, "ctrl_buttons", [])) == 8,  # v24 起历史字段，恒 False
            # v24：6 按钮 / 焦点配色 / 播放信息迁入播控模式
            "ctrl_6_buttons": len(getattr(app, "ctrl_buttons", [])) == 6,
            "focus_tag_colors": (callable(getattr(app, "_apply_focus_highlight", None))
                                 and callable(getattr(app, "_all_treeviews", None))),
            "playinfo_in_pcmode": (getattr(app, "play_scale", None) is not None
                                   and getattr(app, "pc_mode_frame", None) is not None),
            "ctrl_title_position": (hasattr(app, "ctrl_frame")),
            "play_skip": (hasattr(app, "action_play_skip")),
            "smart_play": (hasattr(app, "_stage_find_by_tid")
                           and hasattr(app, "_assign_mseq")),
            "vol_probe": (hasattr(app, "action_volume_probe")
                          and hasattr(app, "_vol_to_device")),
            "focus_tag_highlight": hasattr(app, "_apply_focus_highlight"),
            "progress_reset": hasattr(app, "_reset_play_progress"),
            "geometry": root.geometry(),
        }
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        try:
            (app_dir() / "_gui_probe.json").write_text(
                json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    root.after(200, app.on_close)   # 同时释放嵌入的 Explorer 窗口
    return info


def main():
    root = tk.Tk()
    app = App(root)
    if "--probe" in sys.argv:
        root.after(300, lambda: _probe_mode(root, app))
    elif "--install-driver" in sys.argv and is_admin():
        root.after(400, app.action_install_driver)
    root.mainloop()


if __name__ == "__main__":
    main()
