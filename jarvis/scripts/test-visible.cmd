@echo off
rem Double-click: opens a normal, visible window that runs the pre-flight and the desktop baseline and stays open.
start "JARVIS visible test" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0test-visible.ps1" %*
