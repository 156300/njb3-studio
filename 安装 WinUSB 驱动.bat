@echo off
title NJB3 WinUSB 驱动安装
chcp 936 >nul
cd /d "%~dp0"

rem 自动提权：精简版/家庭版系统无右键管理员入口时也能装驱动
net session >nul 2>&1
if not errorlevel 1 goto :ADMIN_OK
echo 正在申请管理员权限，请在弹出的窗口中点"是"...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
if errorlevel 1 (
  echo.
  echo [提示] 自动提权失败。请右键本文件，选择"以管理员身份运行"。
  echo        若仍失败，说明当前账户被系统限制为无管理员权限，
  echo        请改用管理员账户登录 Windows 后再运行。
  pause
)
exit /b

:ADMIN_OK
echo.
echo ============================================================
echo  NJB3 WinUSB 驱动安装
echo  说明：把 Creative 原厂驱动替换为 Windows 内置 WinUSB
echo ============================================================
echo.
if exist "njb3_driver.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "njb3_driver.ps1" -Install
) else if exist "src\scripts\njb3_driver.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "src\scripts\njb3_driver.ps1" -Install
) else (
  echo [ERROR] 找不到 njb3_driver.ps1，请检查发布包完整性。
)
chcp 936 >nul
echo.
echo 安装完成后请重新插拔 NJB3，再启动软件。
pause
