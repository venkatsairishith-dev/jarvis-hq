#!/bin/bash
# A clean conversation: delete the stored chat (my own test messages), restart the face with the debug port, open the chat. ~45 s.
S="$(cd "$(dirname "$0")" && pwd)"
cd /d/jarvis/hq/jarvis/face/apps/stage-tamagotchi
node "$S/cdp_idb_clear.mjs" >/dev/null 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process jarvis -ErrorAction SilentlyContinue | Where-Object { \$_.Path -like 'D:\\APPS\\jarvis*' } | Stop-Process -Force; Start-Sleep 3; Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue; Start-Process D:\\APPS\\jarvis\\jarvis.exe -ArgumentList '--remote-debugging-port=9333','--remote-debugging-address=127.0.0.1' -WorkingDirectory D:\\APPS\\jarvis; Start-Sleep 24" >/dev/null 2>&1
unset PAGE
node "$S/cdp_ui.mjs" jsclick 'button[aria-label="Open Chat"]' >/dev/null 2>&1
sleep 8
PAGE=chat-floating node "$S/cdp_ui.mjs" dump 2>&1 | grep -q textarea && echo "face reset ok" || echo "face reset: chat not ready"
