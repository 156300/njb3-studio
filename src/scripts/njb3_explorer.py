#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_explorer.py — 把真正的 Windows 资源管理器窗口嵌入 tkinter 容器。

不是自写文件树：通过 Shell 启动一个独立的 explorer.exe 窗口，找到它的
CabinetWClass 顶层窗口，再用 Win32 SetParent 挂到本程序的 Frame 上。
因此左侧看到的是 Windows 原生资源管理器（左侧导航树 + 右侧详细文件栏 +
地址栏 + 右键菜单），不是 ttk.Treeview 仿制品。

只依赖 Python 标准库 ctypes；不引入 pywin32/comtypes，交付包仍是免额外库。
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import ctypes
    from ctypes import wintypes
    _IS_WINDOWS = os.name == "nt"
except Exception:  # pragma: no cover - 非 Windows 上自动回退
    ctypes = None
    wintypes = None
    _IS_WINDOWS = False

if _IS_WINDOWS:
    _u = ctypes.windll.user32
    _k = ctypes.windll.kernel32

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _u.EnumWindows.restype = wintypes.BOOL
    _u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u.IsWindow.argtypes = [wintypes.HWND]
    _u.IsWindow.restype = wintypes.BOOL
    _u.IsWindowVisible.argtypes = [wintypes.HWND]
    _u.IsWindowVisible.restype = wintypes.BOOL
    _u.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    _u.SetParent.restype = wintypes.HWND
    _u.GetParent.argtypes = [wintypes.HWND]
    _u.GetParent.restype = wintypes.HWND
    _u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    _u.MoveWindow.restype = wintypes.BOOL
    _u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u.ShowWindow.restype = wintypes.BOOL
    _u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
    _u.PostMessageW.restype = wintypes.BOOL

    _get_long = getattr(_u, "GetWindowLongPtrW", None)
    _set_long = getattr(_u, "SetWindowLongPtrW", None)
    if _get_long is None:
        _get_long, _set_long = _u.GetWindowLongW, _u.SetWindowLongW
    _get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_long.restype = ctypes.c_ssize_t
    _set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_long.restype = ctypes.c_ssize_t

    _GWL_STYLE = -16
    _WS_CHILD = 0x40000000
    _WS_POPUP = 0x80000000
    _WS_CAPTION = 0x00C00000
    _WS_THICKFRAME = 0x00040000
    _WS_SYSMENU = 0x00080000
    _WS_MAXIMIZEBOX = 0x00010000
    _WS_MINIMIZEBOX = 0x00020000
    _SW_SHOW = 5
    _WM_CLOSE = 0x0010
    _PROCESS_TERMINATE = 0x0001

    _k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k.OpenProcess.restype = wintypes.HANDLE
    _k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k.TerminateProcess.restype = wintypes.BOOL
    _k.CloseHandle.argtypes = [wintypes.HANDLE]
    _k.CloseHandle.restype = wintypes.BOOL


def _explorer_windows() -> list[int]:
    """枚举当前桌面上所有 Explorer 窗口句柄。"""
    found: list[int] = []

    @_WNDENUMPROC
    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(128)
        _u.GetClassNameW(hwnd, buf, 128)
        if buf.value in ("CabinetWClass", "ExploreWClass"):
            found.append(int(hwnd))
        return True

    _u.EnumWindows(cb, 0)
    return found


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _u.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


class NativeExplorerHost:
    """把一个原生 Explorer 窗口挂到 parent 上。

    parent 应为 ttk.Frame 等有真实 HWND 的 tkinter 控件。
    """

    def __init__(self, parent: tk.Widget, path: str | None = None,
                 status_callback=None, add_folder_callback=None):
        self.parent = parent
        self.status_callback = status_callback or (lambda s: None)
        self.add_folder_callback = add_folder_callback
        self.hwnd = None
        self.pid = None
        self.proc = None
        self.path_var = tk.StringVar(value=path or os.path.expanduser("~"))
        self._destroyed = False
        self._poll_after = None

        lf = ttk.LabelFrame(parent, text=" 资源管理器（Windows 原生组件） ", padding=(4, 4))
        lf.pack(fill="both", expand=True)
        self.frame = lf

        bar = ttk.Frame(lf)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="路径:").pack(side="left", padx=(0, 3))
        ent = ttk.Entry(bar, textvariable=self.path_var)
        ent.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ent.bind("<Return>", lambda _e: self.open_path(self.path_var.get()))
        ttk.Button(bar, text="打开", command=lambda: self.open_path(self.path_var.get()),
                   width=6).pack(side="left", padx=(0, 3))
        if add_folder_callback:
            ttk.Button(bar, text="加入此文件夹 MP3",
                       command=lambda: add_folder_callback(self.path_var.get())).pack(side="left")

        self.host = ttk.Frame(lf)
        self.host.pack(fill="both", expand=True)
        self.host.bind("<Configure>", self._on_configure)
        self.host.after_idle(self.start)

    def _status(self, text: str):
        try:
            self.status_callback(text)
        except Exception:
            pass

    def start(self):
        """启动独立 Explorer 进程并异步等待它的窗口出现。"""
        if not _IS_WINDOWS or self._destroyed:
            self._status("当前系统无法嵌入原生资源管理器")
            return
        path = self.path_var.get() or os.path.expanduser("~")
        before = set(_explorer_windows())
        # /separate 确保拿到独立进程；/n,/e,<path> 打开资源管理器的详细视图。
        arg = "/n,/e," + path
        try:
            self.proc = subprocess.Popen(
                ["explorer.exe", "/separate", arg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self._status(f"启动资源管理器失败：{exc}")
            return
        self._status("正在打开 Windows 资源管理器 ...")
        self._wait_for_window(before, 0)

    def _wait_for_window(self, before: set[int], tries: int):
        if self._destroyed:
            return
        new = [h for h in _explorer_windows() if h not in before]
        if new:
            # 优先取与刚启动进程同 PID 的窗口；取不到就用新出现的窗口。
            want = getattr(self.proc, "pid", None)
            hwnd = next((h for h in new if want and _window_pid(h) == want), new[0])
            self._attach(hwnd)
            return
        if tries >= 40:     # 40 * 250ms = 10s
            self._status("未找到可嵌入的资源管理器窗口，请检查 Windows Explorer 是否正常")
            return
        self._poll_after = self.host.after(250, lambda: self._wait_for_window(before, tries + 1))

    def _attach(self, hwnd: int):
        if self._destroyed or not _u.IsWindow(hwnd):
            return
        self.hwnd = hwnd
        self.pid = _window_pid(hwnd)
        style = _get_long(wintypes.HWND(hwnd), _GWL_STYLE)
        # 去掉顶层窗口的标题栏/边框/系统菜单，改成子窗口。
        style = ((style & ~_WS_POPUP & ~_WS_CAPTION & ~_WS_THICKFRAME & ~_WS_SYSMENU
                  & ~_WS_MAXIMIZEBOX & ~_WS_MINIMIZEBOX) | _WS_CHILD)
        _set_long(wintypes.HWND(hwnd), _GWL_STYLE, ctypes.c_ssize_t(style))
        _u.SetParent(wintypes.HWND(hwnd), wintypes.HWND(self.host.winfo_id()))
        self._resize()
        _u.ShowWindow(wintypes.HWND(hwnd), _SW_SHOW)
        self._status("已嵌入 Windows 原生资源管理器（左侧导航树 + 右侧详细文件栏）")

    def _on_configure(self, _event=None):
        self._resize()

    def _resize(self):
        if not self.hwnd or self._destroyed:
            return
        if not _u.IsWindow(wintypes.HWND(self.hwnd)):
            return
        try:
            w = max(200, self.host.winfo_width())
            h = max(160, self.host.winfo_height())
            _u.MoveWindow(wintypes.HWND(self.hwnd), 0, 0, w, h, True)
        except Exception:
            pass

    def open_path(self, path: str):
        """重新打开指定路径（关闭旧嵌入窗口再启动新窗口，最稳）。"""
        if not path or not os.path.exists(path):
            self._status(f"路径不存在：{path}")
            return
        self.path_var.set(path)
        self.close_native()
        self.host.after(120, self.start)

    def close_native(self):
        """释放本控件创建的 Explorer 窗口/进程，绝不关闭用户原有资源管理器。"""
        if self._poll_after is not None:
            try:
                self.host.after_cancel(self._poll_after)
            except Exception:
                pass
            self._poll_after = None
        hwnd, pid = self.hwnd, self.pid
        self.hwnd, self.pid = None, None
        if _IS_WINDOWS and hwnd and _u.IsWindow(wintypes.HWND(hwnd)):
            try:
                _u.PostMessageW(wintypes.HWND(hwnd), _WM_CLOSE, 0, 0)
            except Exception:
                pass
        # 只终止我们 /separate 启动的那个 PID；用户原有的 explorer.exe 不动。
        if _IS_WINDOWS and pid:
            ph = _k.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if ph:
                try:
                    _k.TerminateProcess(ph, 0)
                except Exception:
                    pass
                _k.CloseHandle(ph)
        self.proc = None

    def destroy(self):
        self._destroyed = True
        self.close_native()

    def is_native(self) -> bool:
        return bool(self.hwnd and _IS_WINDOWS and _u.IsWindow(wintypes.HWND(self.hwnd)))


def probe_embed_capability() -> dict:
    """无界面自检：确认 Win32 调用与窗口枚举可用。"""
    if not _IS_WINDOWS:
        return {"ok": False, "reason": "not Windows"}
    try:
        wins = _explorer_windows()
        return {"ok": True, "explorer_windows": len(wins),
                "has_setparent": hasattr(_u, "SetParent")}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
