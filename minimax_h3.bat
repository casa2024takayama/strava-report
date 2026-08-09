@echo off
rem MiniMax H3 launcher (double-click) - runs minimax_h3.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0minimax_h3.ps1" %*
if errorlevel 1 pause
