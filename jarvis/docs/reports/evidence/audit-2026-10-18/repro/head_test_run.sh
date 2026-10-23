#!/bin/sh
S=/tmp/claude-1002/-srv-jarvis-home-hq-neko/f5cf48e8-19a9-50b8-a560-a1752bd6083e/scratchpad/audit
cd $HOME/hq-neko && echo "commit $(git rev-parse HEAD) dirty=$(git status --porcelain | wc -l) start $(date -u +%FT%TZ)" > $S/meta.txt
R="nice -n 19 podman run --rm --cpus=3 --userns=keep-id -e HOME=/tmp -e NEKO_STORAGE_SELECTED_ROOT=/tmp/neko-data -v /srv/jarvis/models/nli:/nli:ro -e RESEARCH_NLI_DIR=/nli/nli-deberta-v3-xsmall -v $HOME/hq-neko:/src:O neko-test sh -c"
$R 'cd /src/jarvis/kernel && python -B -m unittest discover -s tests 2>&1 | tail -25' > $S/kernel.log 2>&1
$R 'cd /src/jarvis/console && python -B -m unittest discover -s tests 2>&1 | tail -8' > $S/console.log 2>&1
$R 'cd /src && git config --global --add safe.directory "*"; for d in plugin/plugins/*/tests jarvis/local_models jarvis/wake/test_wake_listener.py jarvis/tools jarvis/caretaker/tests; do echo "$d: $(timeout 900 python -m pytest $d -q -p no:cacheprovider 2>&1 | tail -1)"; done' > $S/suites.log 2>&1
$R 'cd /src && python -u jarvis/evals/neko95/bench/bench.py 2>&1 | grep -E "^(FAIL|ERROR) |pass\"|saved"; python jarvis/evals/neko95/bench/scorecard.py 2>&1 | tail -20' > $S/bench.log 2>&1
echo "end $(date -u +%FT%TZ)" >> $S/meta.txt; echo done > $S/done
