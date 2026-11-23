# 架构说明

## 1. 分层

```
┌───────────────────────────────────────────────────────────┐
│  新版界面层  src/ui_next/                                  │
│  StudioApp(njb3_gui.App)  ·  njb3_dock  ·  njb3_theme      │
│  只覆盖 UI 与调度，不重写业务逻辑                          │
├───────────────────────────────────────────────────────────┤
│  业务层  src/scripts/njb3_gui.py                           │
│  连接管理、曲库/清单渲染、传输队列、播放模式、事件派发      │
├───────────────────────────────────────────────────────────┤
│  协议层  src/scripts/njb3_pde.py                           │
│  CBSU 帧、capture/release、命令码、分批游标、UCS-2BE        │
├───────────────────────────────────────────────────────────┤
│  USB 层  pyusb + libusb-1.0.dll（内置于绿色 exe）          │
└───────────────────────────────────────────────────────────┘
```

**核心设计决策**：新版 UI 通过子类化复用旧 GUI 的全部业务代码。
`StudioApp` 覆盖 `_build_ui()` 与少量事件方法，`scripts/` 中的业务文件保持不动。
这样界面可以大改而不动已经真机验证过的协议与业务逻辑。

## 2. 关键模块

| 文件 | 职责 |
|---|---|
| `src/scripts/njb3_pde.py` | `NJB3Device`：USB 打开/关闭、命令收发、曲库/播放列表/队列读写、曲目上传下载。无 GUI 依赖，可单独测试。 |
| `src/scripts/njb3_gui.py` | `App`：tkinter 业务主体。连接管理（持久连接 + 断开 + 掉线弹窗）、曲库/清单/队列渲染、事件派发。 |
| `src/ui_next/njb3_studio.py` | `StudioApp`：三工作台布局、 Dock 集成、按钮归属、播放调度、曲库缓存代理。 |
| `src/ui_next/njb3_dock.py` | 自研停靠框架：`paned` / `tabs` / `float` 三种模式，14px 折叠栏、4px sash，布局持久化。 |
| `src/ui_next/njb3_theme.py` | 设计令牌与 4 套皮肤、系统主题跟随、WCAG AA 对比度。 |
| `src/scripts/make_cat.py` | 用 `ctypes` 复刻 libwdi 的 CAT 生成与签名算法（含二进制 SHA1 digest）。 |
| `src/scripts/njb3_driver.ps1` | WinUSB 驱动安装：证书信任、生成 CAT、`pnputil` 绑定、自检。 |

## 3. 设备代理与缓存

`StudioApp._worker` 把真实设备包装成 `_SafePlaylistDevice` 代理，代理负责两件事：

1. **容错**：播放清单成员读取超时按空清单跳过，不中断连接。
2. **曲库缓存**：
   - `read_id()`：读取并缓存设备 16 字节序号。
   - `list_tracks()`：若该序号已有缓存且未强制 → `extended=False` 快读 + 按 trackid 补回
     `filename` / `folder`；否则 `extended=True` 完整读并写缓存。
   - 缓存文件 `njb3_library_cache.json` 位于程序目录，`version=1`，
     结构 `{"devices": {serial: {"saved", "count", "fields": {trackid: {filename, folder}}}}}`，
     原子写（`.tmp` + `os.replace`）。

基础字段（标题/艺人/时长等）**始终取设备实时值**，因此缓存只影响慢字段，不会掩盖设备上的改动。
手动「刷新完整曲库和播放清单(速度慢)」置位 `_force_full_library_read`，由代理消费后清除。

## 4. 事件归属

界面存在多份复制的树控件（`lib_tree` / `lib_tree2`，`pl_tree` / `pl_tree2`，`pl_songs` / `pl_songs2`）。
右键菜单命令在**生成菜单时绑定事件来源树**，不能依赖事后恢复的临时引用——
这是 v42 修复「删除/改名串到别的树」的根因。所有同类回调都遵循这条规则。

## 5. 播放曲终检测

真机事实：设备播放全程 `state=0(playing)`，播完不报 stopped；`play_track` 命令有 0.5~1.1s 延迟。
因此播放推进采用**单一本地计时 / ended 触发**，不使用高频轮询或提前量
（v43 的 300ms 轮询 + 0.35s 提前量会造成乱跳，已在 v44 撤销）。

## 6. 真机纪律

任何访问真机的脚本必须 `try/finally`：

```
PLAY_STOP → clear queue → recover → close → 复核 state=1 (VERIFY OK)
```

且**禁止用管道截断诊断脚本输出**（例如 `| head`），否则进程被强杀会跳过归还流程导致设备卡死。
`tools/diagnostics/` 中的脚本均遵循此纪律，可作为参考模板。
