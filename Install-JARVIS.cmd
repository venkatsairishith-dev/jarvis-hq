@echo off
rem One-click install of JARVIS: double-click this file. It runs jarvis\scripts\Install-JARVIS.ps1 (read it first if you like).
rem Pass the same options, e.g.  Install-JARVIS.cmd -NoModels   or   Install-JARVIS.cmd -Verify
title Install JARVIS
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0jarvis\scripts\Install-JARVIS.ps1" %*
echo.
pause
