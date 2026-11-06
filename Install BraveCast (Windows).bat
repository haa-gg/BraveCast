@echo off
REM Double-click this file in Windows File Explorer to launch the graphical BraveCast installer.
REM Automatically requests Administrator privileges via Windows UAC.

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%SCRIPT_DIR%windows\install.ps1\" -Gui'"
