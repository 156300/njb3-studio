# 第三方组件

本项目不捆绑专有软件。以下组件可能以源码依赖、打包依赖或运行时形式出现，版权归各自作者所有。

| 组件 | 用途 | 许可证 | 项目 |
|---|---|---|---|
| Python 3.13 | 运行时 / 开发 | PSF License | https://www.python.org/ |
| pyusb | USB 设备访问 | BSD-3-Clause | https://github.com/pyusb/pyusb |
| libusb-1.0 | USB 后端 DLL | LGPL-2.1-or-later | https://libusb.info/ |
| libusb-package | 内置 libusb DLL 的 Python 包 | LGPL-2.1-or-later（上游） | https://pypi.org/project/libusb-package/ |
| PyInstaller | 绿色单文件打包 | GPL-2.0-or-later（引导程序有例外条款） | https://pyinstaller.org/ |
| Pillow | 图像处理 | HPND | https://python-pillow.org/ |
| pywin32 | Windows API 绑定 | PSF-like | https://github.com/mhammond/pywin32 |
| tkinter / Tcl-Tk | 界面控件 | Tcl/Tk 许可证 | https://www.tcl.tk/ |
| Microsoft WinUSB | 系统内置 USB 驱动 | Microsoft 许可，随 Windows 分发 | 不随本项目分发 |

## 参考实现

- **libnjb**（https://github.com/libnjb/libnjb）：PDE 协议与曲库结构的开源参考实现。
  本项目**没有复制其代码**，仅依据公开协议文档与行为观察自研实现。
- **libwdi / Zadig**（https://github.com/pbatard/libwdi）：CAT 签名算法的参考。
  `src/scripts/make_cat.py` 是独立用 Python `ctypes` 重写的实现。

## 未包含的内容

以下受版权保护的素材**不在本仓库中**，请通过各自官方渠道获取：

- Creative Nomad Jukebox 3 原厂驱动（ctpdusb.sys 等）与 CMS5 软件；
- 设备固件镜像、原厂用户手册全文、ARM 数据手册全文；
- 任何第三方可执行工具（如 Zadig）的二进制文件。

如果需要 Zadig，请到 https://zadig.akeo.ie/ 自行下载。
