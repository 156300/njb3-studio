# 贡献与开发约定

欢迎提交问题、功能建议与代码改进。本项目由 AI 辅助开发，代码风格以「可读、可测、少依赖」为优先。

## 开发环境

- Windows 10/11（64 位），Python 3.13，`pip install -r src/scripts/requirements.txt`
- 建议先跑通 `python src/ui_next/njb3_studio.py`，再改代码。
- 详细构建步骤见 [docs/构建与开发.md](docs/构建与开发.md)。

## 代码组织原则

- `src/scripts/njb3_pde.py`：**只读协议层**，不依赖 tkinter。所有 USB 细节集中在此。
- `src/scripts/njb3_gui.py`：**业务与原始界面**，保持向后兼容。
- `src/ui_next/`：新版界面。通过子类化复用业务逻辑，**原则上不修改 `src/scripts/` 中的业务文件**。
  新增界面功能优先在 `StudioApp` 中覆盖方法或扩展代理对象。
- 真机操作（写设备）一律走 `try/finally`：退出前必须 `PLAY_STOP` → `clear queue` → `recover` → `close`，
  复核设备状态正常后才能结束脚本。参考 `tools/diagnostics/diag_safe_probe.py`。

## 测试约定

```powershell
python -m pytest tests        # 或直接逐个运行 tests/test_*.py
```

- 新增功能必须附回归测试；测试不得连接真机、不得依赖个人路径。
- 真机验证脚本放在 `tools/diagnostics/`，必须只读或完整归还设备，且不得用管道截断输出
  （曾被 `| head` 强杀导致设备未归还）。
- 提交前确认 `tests/` 全部通过（当前 19 套、约 500 项断言）。

## 提交规范

- Commit message 使用中文或英文祈使句，简述改动，例如 `fix: 曲库缓存命中时补齐 folder 字段`。
- 功能改动同步更新 `CHANGELOG.md`；界面改动同步更新 `docs/新版UI说明.md`。
- 不要提交本机生成物：曲库缓存 `njb3_library_cache.json`、播放队列缓存、日志、配置中的个人数据、
  构建的 `build/` 中间目录等（见 `.gitignore`）。

## 后续版本同步到发布目录

`D:\Ai\njb3`（本仓库工作副本）是**发布专用目录**，以后每次迭代按以下清单同步：

1. `src/ui_next/njb3_studio.py`、`njb3_theme.py`、`njb3_dock.py`、`njb3_studio.spec` ← 开发目录对应文件。
2. `src/scripts/` 中被修改的业务/协议/驱动脚本 ← 开发目录对应文件；`scripts/` 的备份文件与历史版本不要带入。
3. `tests/` ← 新增或修改的测试；`tools/diagnostics/` ← 新增的诊断脚本（须脱敏）。
4. `docs/新版UI说明.md` ← 逐项版本说明；`CHANGELOG.md` ← 新增版本条目。
5. 打包新 exe 放入 `releases/`，重新生成 zip 与 `SHA256SUMS.txt`，更新 `releases/README.md` 的版本号。
6. 更新 `README.md` 中的版本号、快速开始路径与性能数字。
7. 全量回归、确认无隐私数据后提交并推送；在 GitHub 上创建同名 Release，上传 zip 作为附件。

## 隐私与合规

- 不提交设备序列号、曲库缓存、个人音乐目录、机器名、用户名、安装日志等任何个人信息。
- 不提交 Creative 专有驱动、固件、CMS5 安装器与受版权保护的手册原文。
- 自签名证书仅用于本机驱动测试，不要分发私钥。

---

对协议细节感兴趣的读者，建议从 [docs/AI_DEVELOPMENT_JOURNEY.md](docs/AI_DEVELOPMENT_JOURNEY.md) 开始，
那里按时间线记录了从零摸清 NJB3 通信到完成绿色发布的全过程。
