#!/bin/sh
# Exit 0 if the newest nightly result file is at most 36 hours old (or the job has not had its first chance to run yet).
DIR=/home/ubuntu/jarvis/bench-results
NEWEST=$(ls -1t "$DIR"/*.json 2>/dev/null | head -1)
if [ -z "$NEWEST" ]; then exit 0; fi
AGE=$(( $(date +%s) - $(stat -c %Y "$NEWEST") ))
if [ "$AGE" -gt 129600 ]; then echo "newest nightly result is $((AGE / 3600)) hours old"; exit 1; fi
exit 0
