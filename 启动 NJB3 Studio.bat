@echo off
title NJB3 Studio
cd /d "%~dp0"
if exist "njb3_studio.exe" (
  start "" "njb3_studio.exe"
) else if exist "releases\njb3_studio.exe" (
  start "" "releases\njb3_studio.exe"
) else (
  echo [ERROR] njb3_studio.exe not found. Keep this bat beside the exe.
  pause
)
