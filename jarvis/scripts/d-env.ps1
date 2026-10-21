# One storage rule (owner, 2026-10-21): nothing outside D:. Dot-source this from every start script.
foreach ($d in 'D:\jarvis\tmp','D:\jarvis\cache\npm','D:\jarvis\cache\pip','D:\jarvis\cache\uv','D:\jarvis\cache\hf','D:\jarvis\cache\xdg') { New-Item -ItemType Directory -Force $d | Out-Null }
$env:TEMP = 'D:\jarvis\tmp'; $env:TMP = 'D:\jarvis\tmp'
$env:npm_config_cache = 'D:\jarvis\cache\npm'; $env:PIP_CACHE_DIR = 'D:\jarvis\cache\pip'; $env:UV_CACHE_DIR = 'D:\jarvis\cache\uv'
$env:HF_HOME = 'D:\jarvis\cache\hf'; $env:XDG_CACHE_HOME = 'D:\jarvis\cache\xdg'
