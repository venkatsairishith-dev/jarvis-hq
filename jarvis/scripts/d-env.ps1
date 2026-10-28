# One storage rule (owner, 2026-10-21; footprint budget D50, 2026-10-25): nothing outside D:. Dot-source this from every start script.
# MIRRORS storage_env() in jarvis/supervisor/budget.py (jarvis/supervisor/test_budget.py compares the two): change both together.
# APP_USER_DATA_PATH is the face's Electron profile; the face's main process also sets userData/temp/logs there itself (storage-lock.ts).
foreach ($d in 'D:\jarvis\tmp','D:\jarvis\cache\hf','D:\jarvis\cache\hf\hub','D:\jarvis\cache\xdg','D:\jarvis\cache\pip','D:\jarvis\cache\uv','D:\jarvis\cache\npm','D:\jarvis\tools\pnpm','D:\jarvis\cache\pnpm-store','D:\jarvis\cache\ms-playwright','D:\jarvis\cache\electron','D:\jarvis\cache\electron-builder','D:\jarvis\models\ollama','D:\jarvis\cache\llama','D:\jarvis\data\face\user-data') { New-Item -ItemType Directory -Force $d | Out-Null }
$env:TEMP = 'D:\jarvis\tmp'
$env:TMP = 'D:\jarvis\tmp'
$env:TMPDIR = 'D:\jarvis\tmp'
$env:HF_HOME = 'D:\jarvis\cache\hf'
$env:HF_HUB_CACHE = 'D:\jarvis\cache\hf\hub'
$env:XDG_CACHE_HOME = 'D:\jarvis\cache\xdg'
$env:PIP_CACHE_DIR = 'D:\jarvis\cache\pip'
$env:UV_CACHE_DIR = 'D:\jarvis\cache\uv'
$env:npm_config_cache = 'D:\jarvis\cache\npm'
$env:PNPM_HOME = 'D:\jarvis\tools\pnpm'
$env:npm_config_store_dir = 'D:\jarvis\cache\pnpm-store'
$env:PNPM_STORE_DIR = 'D:\jarvis\cache\pnpm-store'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\jarvis\cache\ms-playwright'
$env:ELECTRON_CACHE = 'D:\jarvis\cache\electron'
$env:ELECTRON_BUILDER_CACHE = 'D:\jarvis\cache\electron-builder'
$env:OLLAMA_MODELS = 'D:\jarvis\models\ollama'
$env:LLAMA_CACHE = 'D:\jarvis\cache\llama'
$env:APP_USER_DATA_PATH = 'D:\jarvis\data\face\user-data'

# Owner switches (NOT storage: supervisor/test_budget.py leaves these out of the mirror check). Dot-sourced by jarvis-start.ps1 (tool server, gateways) and start-node.ps1 (node).
# JARVIS_SCREEN_TEXT_TO_MODEL=1: the owner said "Yes, turn it on" (twice, relayed 2026-10-28). Window and page text may go to the model, and ONLY through the privacy gateway: names, numbers and
# dates become reversible tags ([PERSON_1]), secrets (passwords, card numbers, keys, codes) become [REDACTED:kind] and are never sent or kept (D73, plugins/_shared/screen_text.py).
# To turn it off again: delete the next line (or set it to 0) and restart the stack and the node.
$env:JARVIS_SCREEN_TEXT_TO_MODEL = '1'
