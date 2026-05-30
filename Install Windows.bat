@echo off
:: Trader's Little Jedi — Windows installer launcher
:: Double-click this file to install.

title Trader's Little Jedi Installer

echo.
echo  Launching installer...
echo  (A blue PowerShell window will open — that is normal.)
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"

if %errorlevel% neq 0 (
    echo.
    echo  Installer exited with an error.
    pause
)
