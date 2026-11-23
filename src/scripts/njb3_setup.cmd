@echo off
setlocal
set "ROOT=%~dp0"

:MENU
cls
echo ============================================================
echo   Nomad Jukebox 3  -  Windows 11  安装与传输控制台
echo ============================================================
echo.
echo   [1] 安装 Creative 原厂驱动（USB + 1394 设备端，需管理员）
echo   [2] 配置 IEEE-1394 火线卡（VIA VT630x/VT6315N / TI TSB43ABxx）
echo   [3] 安装 CMS5 + NJB2/NJB3 插件（先演练）
echo   [4] 安装后体检（只读）
echo   [5] 打开传输工具帮助 / 自检
echo   [6] 打开文档目录
echo   [0] 退出
echo.
set /p CH=请选择: 

if "%CH%"=="1" goto DRV
if "%CH%"=="2" goto FW
if "%CH%"=="3" goto CMS
if "%CH%"=="4" goto VERIFY
if "%CH%"=="5" goto TRANSFER
if "%CH%"=="6" goto DOCS
if "%CH%"=="0" goto END
echo 无效选择。
pause
goto MENU

:DRV
cls
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%run_admin.ps1" -Script "%ROOT%install_win11_driver.ps1"
pause
goto MENU

:FW
cls
echo 第一步：只读检测火线卡...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%install_1394_host.ps1" -OnlyCheck
echo.
set /p Y=检测到卡后，是否继续安装？(y/N): 
if /i "%Y%"=="y" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%run_admin.ps1" -Script "%ROOT%install_1394_host.ps1"
pause
goto MENU

:CMS
cls
echo 默认演练模式（不会真的安装）:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%install_cms5.ps1"
echo.
set /p Y=确认无误，是否真正安装？(y/N): 
if /i "%Y%"=="y" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%run_admin.ps1" -Script "%ROOT%install_cms5.ps1" -Execute
pause
goto MENU

:VERIFY
cls
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%verify_install.ps1"
pause
goto MENU

:TRANSFER
cls
pushd "%ROOT%"
python njb3_transfer.py doctor
echo.
python njb3_transfer.py self-test
echo.
echo 可用命令示例：
echo   python njb3_transfer.py list
echo   python njb3_transfer.py m3u-import --file ..\config\example_playlists\roadtrip.m3u --dry-run
echo   python njb3_transfer.py upload --src 音乐目录 --dry-run
popd
pause
goto MENU

:DOCS
start "" "%ROOT%..\docs"
goto MENU

:END
endlocal
