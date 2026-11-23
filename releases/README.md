# 发布包

## 当前版本：0.1.0-261123

版本号规则：`主版本.次版本.修订版本-编译日期(YYMMDD)`。
`0` = 测试版；`1` = 第一版；`0` = 初版（后续修正递增该位）；`261123` = 2026-11-23 编译。

| 文件 | 说明 |
|---|---|
| `njb3-studio-0.1.0-261123-win-x64.zip` | **推荐下载**。完整绿色包，21.6 MB，含主程序 + 驱动安装脚本 + 签名工具 + 使用说明 |
| `njb3_studio.exe` | 主程序单文件（14 MB），可单独运行，但首次使用仍需驱动包里的脚本安装 WinUSB |
| `SHA256SUMS.txt` | 上面两个文件的 SHA-256 校验值 |

## 绿色包内容

解压后目录是扁平的，所有文件放在同一层：

```
启动 NJB3 Studio.bat        主程序启动入口（GBK+CRLF 编码）
安装 WinUSB 驱动.bat        自动 UAC 提权，安装 WinUSB（GBK+CRLF 编码）
njb3_studio.exe             主程序（PyInstaller 单文件，内置 libusb）
njb3_driver.ps1             驱动安装脚本 v8（PFX 私钥签名 + 精简系统兼容）
njb3_winusb.inf / .cat      WinUSB 绑定文件
make_cat.exe                签名 CAT 生成器 v8（支持 PFX，源码在 src/scripts/make_cat.py）
使用说明.txt                快速开始（GBK 编码）
LICENSE.txt                 GNU GPL v3 许可
```

无需安装 Python、VC++ 运行库或其他组件；唯一前置条件是**一次性安装 WinUSB 驱动**
（双击 bat 会自动弹 UAC 提权；若账户无管理员权限请改用管理员账户登录）。
程序内置 `libusb-1.0.dll`（LGPL），许可证说明见仓库 `docs/THIRD_PARTY.md`。

> 注意：包内 `.bat` 为 GBK(ANSI)+CRLF 编码，中文 Windows 下可直接双击执行。
> 请勿用记事本另存为 UTF-8，否则中文会乱码导致脚本无法执行。

## 校验

```powershell
Get-FileHash .\njb3-studio-0.1.0-261123-win-x64.zip -Algorithm SHA256
```

结果与 `SHA256SUMS.txt` 一致即可。

## 发布到 GitHub

本目录作为发布源。在 GitHub 上创建 Release 时：

- Tag：`v0.1.0-261123`
- Title：`NJB3 Studio 0.1.0-261123 — 绿色版`
- 附件：上传 `njb3-studio-0.1.0-261123-win-x64.zip`，以及可选的 `njb3_studio.exe`
- Release Notes：从 `CHANGELOG.md` 的 `0.1.0-261123` 小节摘取

后续版本迭代时：先更新源码与文档，重新打包并替换本目录文件，
重算 `SHA256SUMS.txt`，再按 [CONTRIBUTING.md](../CONTRIBUTING.md) 的流程发布。
