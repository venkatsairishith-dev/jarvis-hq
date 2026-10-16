"""Read-only diagnostics of this Windows PC for Neko's repair workflow (2026-10-16). Nothing here changes the PC.

Each check is a fixed PowerShell script in this file; the only input is a short name (a process, a service), passed as an
environment variable. Text that came from the screen or from event messages (window text, error messages) is written to
a LOCAL file under outputs/neko-local/ and only counts and safe facts go back to the kernel: screen contents never leave
the laptop unless the owner asks. The Neko plugin on this laptop reads the local file and deletes it.
"""
import json
import os
import re
import secrets
import time

from . import runtime
from .paths import local_result_dir

NAME = re.compile(r"^[A-Za-z0-9_. :()-]{0,80}$")

PROCESSES = r"""
Get-Process | Sort-Object CPU -Descending | Select-Object -First 25 | ForEach-Object {
  [pscustomobject]@{ name = $_.ProcessName; id = $_.Id; cpu_s = [math]::Round([double]$_.CPU, 1);
                     mem_mb = [math]::Round($_.WorkingSet64 / 1MB); window = ($_.MainWindowHandle -ne 0);
                     responding = $(if ($_.MainWindowHandle -ne 0) { $_.Responding } else { $null }) } } | ConvertTo-Json -Compress
"""
APP_ERRORS = r"""
$since = (Get-Date).AddHours(-[int]$env:JARVIS_DIAG_HOURS)
$ev = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Level = 1,2; StartTime = $since } -MaxEvents 200 -ErrorAction SilentlyContinue
if ($env:JARVIS_DIAG_NAME) { $n = $env:JARVIS_DIAG_NAME; $ev = $ev | Where-Object { $_.ProviderName -like "*$n*" -or $_.Message -like "*$n*" } }
@($ev | Select-Object -First 20 | ForEach-Object {
  [pscustomobject]@{ time = $_.TimeCreated.ToString('s'); provider = $_.ProviderName; id = $_.Id; level = $_.LevelDisplayName;
                     message = ($_.Message -replace '\s+', ' ').Substring(0, [Math]::Min(400, ($_.Message -replace '\s+', ' ').Length)) } }) | ConvertTo-Json -Compress
"""
CRASHES = r"""
$since = (Get-Date).AddDays(-7)
$ev = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Id = 1000, 1002; StartTime = $since } -MaxEvents 50 -ErrorAction SilentlyContinue
if ($env:JARVIS_DIAG_NAME) { $n = $env:JARVIS_DIAG_NAME; $ev = $ev | Where-Object { $_.Message -like "*$n*" } }
@($ev | Select-Object -First 15 | ForEach-Object {
  $p = $_.Properties
  [pscustomobject]@{ time = $_.TimeCreated.ToString('s'); kind = $(if ($_.Id -eq 1000) { 'crash' } else { 'hang' });
                     app = [string]$p[0].Value; app_version = [string]$p[1].Value;
                     module = $(if ($_.Id -eq 1000) { [string]$p[3].Value } else { '' });
                     code = $(if ($_.Id -eq 1000) { [string]$p[6].Value } else { '' }) } }) | ConvertTo-Json -Compress
"""
SERVICES = r"""
$s = Get-Service -Name $env:JARVIS_DIAG_NAME -ErrorAction SilentlyContinue
if (-not $s) { $s = Get-Service -DisplayName "*$($env:JARVIS_DIAG_NAME)*" -ErrorAction SilentlyContinue | Select-Object -First 5 }
@($s | ForEach-Object { [pscustomobject]@{ name = $_.Name; display = $_.DisplayName; status = $_.Status.ToString(); start = $_.StartType.ToString() } }) | ConvertTo-Json -Compress
"""
DISK = r"""
@(Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object {
  [pscustomobject]@{ drive = $_.Name; free_gb = [math]::Round($_.Free / 1GB, 1); total_gb = [math]::Round(($_.Used + $_.Free) / 1GB, 1) } }) | ConvertTo-Json -Compress
"""
NETWORK = r"""
$up = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { $_.InterfaceDescription })
$dns = $false; try { [void][System.Net.Dns]::GetHostAddresses('www.microsoft.com'); $dns = $true } catch { }
$tcp = $false; try { $c = New-Object System.Net.Sockets.TcpClient; $t = $c.ConnectAsync('1.1.1.1', 443); $tcp = $t.Wait(3000) -and $c.Connected; $c.Close() } catch { }
[pscustomobject]@{ adapters_up = $up.Count; adapters = $up; dns_ok = $dns; internet_tcp_ok = $tcp } | ConvertTo-Json -Compress
"""
APP_INFO = r"""
@(Get-Process -Name $env:JARVIS_DIAG_NAME -ErrorAction SilentlyContinue | ForEach-Object {
  $path = $null; $ver = $null; try { $path = $_.Path; $ver = $_.MainModule.FileVersionInfo.ProductVersion } catch { }
  [pscustomobject]@{ id = $_.Id; path = $path; version = $ver; window_title = $_.MainWindowTitle; responding = $_.Responding;
                     started = $(try { $_.StartTime.ToString('s') } catch { $null }); mem_mb = [math]::Round($_.WorkingSet64 / 1MB) } }) | ConvertTo-Json -Compress
"""
# Every top-level window of the process, including dialogs (an error dialog is its own window), with its text.
WINDOW_TEXT = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$ids = @(Get-Process -Name $env:JARVIS_DIAG_NAME -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
if (-not $ids) { '[]'; exit }
$A = [System.Windows.Automation.AutomationElement]
$out = @()
foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
  if ($ids -notcontains $w.Current.ProcessId) { continue }
  $texts = @(); $buttons = @()
  foreach ($e in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
    if ($e.Current.IsPassword) { continue }
    $t = $e.Current.ControlType.ProgrammaticName; $n = $e.Current.Name
    if (-not $n) { continue }
    $c = $e.Current.ClassName      # classic Win32 dialogs (MessageBox) come through as Pane: their class says what they are
    if ($t -eq 'ControlType.Button' -or $c -eq 'Button') { $buttons += $n }
    elseif ($t -in 'ControlType.Text', 'ControlType.Edit', 'ControlType.Document' -or $c -in 'Static', 'Edit', 'RichEdit20W') { $texts += $n }
    if ($texts.Count -gt 60) { break }
  }
  $out += [pscustomobject]@{ title = $w.Current.Name; class = $w.Current.ClassName; dialog = ($w.Current.ClassName -eq '#32770');
                             texts = $texts; buttons = $buttons }
}
@($out) | ConvertTo-Json -Compress -Depth 4
"""
SCRIPTS = {"processes": PROCESSES, "app_errors": APP_ERRORS, "crashes": CRASHES, "services": SERVICES, "disk": DISK,
           "network": NETWORK, "app_info": APP_INFO, "window_text": WINDOW_TEXT}
NEEDS_NAME = {"services", "app_info", "window_text"}
LOCAL_ONLY = {"app_errors", "window_text"}           # free text from the screen or from event messages
SENSITIVE = re.compile(r"(?i)keepass|1password|bitwarden|lastpass|dashlane|enpass|wallet|metamask|exodus|ledger|banking|netbank|paypal")


def _json(text):
    text = (text or "").strip()
    if not text:
        return []
    v = json.loads(text)
    return v if isinstance(v, list) else [v] if v is not None else []


def run(p: dict, runner=None) -> dict:
    op, name = str(p.get("op", "")), str(p.get("target", ""))
    if op not in SCRIPTS:
        return {"ok": False, "error": "unknown diagnostic"}
    if not NAME.match(name) or (op in NEEDS_NAME and not name):
        return {"ok": False, "error": "a short app or service name is needed" if op in NEEDS_NAME else "bad name"}
    if SENSITIVE.search(name):
        return {"ok": False, "error": "refused: a password manager, wallet or bank"}
    runner = runner or runtime.powershell
    r = runner(SCRIPTS[op], {"JARVIS_DIAG_NAME": name, "JARVIS_DIAG_HOURS": "24"}, 60)
    if r.returncode != 0:
        return {"ok": False, "error": f"the {op} check failed (exit {r.returncode})"}
    try:
        rows = _json(r.stdout)
    except ValueError:
        return {"ok": False, "error": f"the {op} check returned something unreadable"}
    if op == "window_text":
        rows = [w for w in rows if not SENSITIVE.search(str(w.get("title", "")))]
    if op not in LOCAL_ONLY:
        return {"ok": True, "check": op, "count": len(rows), "rows": rows[:40]}
    path = save_local(op, rows)
    summary = {"windows": len(rows), "dialogs": sum(bool(w.get("dialog")) for w in rows)} if op == "window_text" else \
              {"events": len(rows), "providers": sorted({str(e.get("provider", ""))[:60] for e in rows})[:10],
               "event_ids": sorted({e.get("id") for e in rows if isinstance(e.get("id"), int)})[:20]}
    return {"ok": True, "check": op, "local_result": path, "summary": summary,
            "note": "the text stayed on this laptop (local_result); only this summary went to the kernel"}


def save_local(kind: str, data) -> str:
    """Screen or message text for the Neko plugin on this laptop only. The plugin reads it once and deletes it."""
    d = local_result_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path
