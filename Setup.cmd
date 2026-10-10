@echo off
rem DiagTrack setup for Windows 11 and Windows Server 2016 or newer.
rem Double-click this file. Windows asks for administrator rights once.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\windows\bootstrap.ps1" %*
exit /b %errorlevel%
