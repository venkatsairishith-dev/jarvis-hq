#!/bin/bash
# usage: bench_one.sh <model-label> <goal-number 1..5> [maxSeconds]   (the pin file already names the model)
S="$(cd "$(dirname "$0")" && pwd)"; label="$1"; n="$2"; max="${3:-240}"
case "$n" in
  1) goal="Open Notepad and write a shopping list: milk, eggs, bread, rice." ;;
  2) goal="What is on my screen right now?" ;;
  3) goal="Make a 3-slide deck about our week and show it to me." ;;
  4) goal="Read the invoice invoice-2026-0522.png in the invoice-test folder of my JARVIS outputs and tell me the total." ;;
  5) goal="Two things at once: open Calculator and also tell me the weather in Paris." ;;
esac
"$S/face_reset.sh"
since=$(date +%s)
cd /d/jarvis/hq/jarvis/face/apps/stage-tamagotchi
row=$(node "$S/bench_run.mjs" "$goal" "$max" 2>&1 | tail -1)
shots="$S/shots_bench"; mkdir -p "$shots"
powershell -NoProfile -ExecutionPolicy Bypass -File "$S/shot_windows.ps1" "jarvis" "$shots" >/dev/null 2>&1
latest=$(ls -t "$shots"/win*.png 2>/dev/null | head -3)
i=0; for f in $latest; do i=$((i+1)); cp "$f" "$shots/${label// /_}-g$n-$i.png"; done
echo "{\"model\": \"$label\", \"goal_no\": $n, \"run\": $row}" >> "$S/bench_rows.jsonl"
echo "$label g$n: $(echo "$row" | cut -c1-1200)"
powershell -NoProfile -ExecutionPolicy Bypass -File "$S/close_launched.ps1" -Since "$since" 2>&1 | tail -3
