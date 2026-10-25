# On-screen benchmark driver (2026-10-25)

These scripts drive the real face the way an owner would: they type the goal into the chat box through the face's debug port and time what the face sends to the
privacy gateway. They do not call a model and do not choose tools. Needs the face started with `--remote-debugging-port=9333` (loopback), Node, and the face's `node_modules`.

- `face_reset.sh`: deletes the stored test chat, restarts the face, opens the chat (a clean conversation per run; clearing in place did not reset what the chat sends).
- `bench_one.sh <label> <1..5> [seconds]`: reset, run one goal (wording in the script), save a row to `bench_rows.jsonl`, save screenshots of the face windows, close the windows
  JARVIS recorded as opened (`close_launched.ps1`: only recorded handles).
- `bench_run.mjs`: the run itself (typing, request/response timing, tool names called, errors on screen).
- `make_results.py`: rows -> `../results/onscreen-<date>.json`.
- Pin one provider with the gateway's pin file: one line `dahl`, `dahl <model id>`, `groq`, `cerebras` or `local`. Start the gateway with `JARVIS_GATEWAY_PIN_FILE=<path>` to keep the file
  outside the secrets folder. Delete the file and restart the gateway normally when done.
