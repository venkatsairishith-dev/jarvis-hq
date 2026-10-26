"""JARVIS answers the Playwright extension's own "allow this connection" page by itself (owner, 2026-10-25: "JARVIS clicks the on-screen permission dialogs
for its own test windows / the Playwright extension connect page"; the PIN approvals stay the owner's). Windows.

What the real page looks like (read with UI Automation on 2026-10-26, Chrome with the Playwright MCP Bridge extension):
  a Chrome tab (title "Welcome") whose address is chrome-extension://<extension id>/connect.html?mcpRelayUrl=...&client={"name":"jarvis"} and whose page says
  '"jarvis" is trying to connect to the Playwright Extension.', then one row for every open tab: its title, its address and a button "Allow & select"
  (ControlType Button, class "button primary", an Invoke pattern). There is no plain "Allow": the connection is made by allowing ONE tab.
Which tab: only a row whose address is that extension's own connect.html AND names the relay port of the MCP server this launch started (older connect
tabs are dead relays of earlier attempts: a click there connects nothing, as seen live). A row for any other tab (the owner's own pages: mail, a video, a document) is never pressed, because
allowing a tab hands the client that tab's page. And only for the client name "jarvis": a connection request from any other program is left alone.
The page also prints a connection token. This module never reads, returns or logs any text of the page except the two checks above.
"""
import subprocess
import threading
import time

EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
CLIENT_SAYS = '"jarvis" is trying to connect to the Playwright Extension.'
BUTTON = "Allow & select"

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]; $TS = [System.Windows.Automation.TreeScope]; $CT = [System.Windows.Automation.ControlType]
$says = $env:JARVIS_CONNECT_SAYS; $btnName = $env:JARVIS_CONNECT_BUTTON; $mine = 'chrome-extension://' + $env:JARVIS_CONNECT_EXT + '/connect.html'
$done = 'none'
# The relay port of the MCP server THIS launch started: the connect page of an earlier, dead attempt names another port and is never pressed.
$ports = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match 'playwright.mcp.cli\.js' -and $_.CommandLine -match '--extension' } |
  ForEach-Object { Get-NetTCPConnection -OwningProcess $_.ProcessId -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalPort } })
if ($ports.Count -eq 0) { 'none'; exit }
foreach ($w in $AE::RootElement.FindAll($TS::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
  $pn = try { (Get-Process -Id $w.Current.ProcessId -ErrorAction Stop).ProcessName } catch { '' }
  if ($pn -ne 'chrome') { continue }
  foreach ($doc in $w.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::Document)))) {
    $asked = $false; $lastMine = $false; $target = $null
    foreach ($e in $doc.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
      $c = $e.Current
      if ($c.ControlType -eq $CT::Text) {
        if ($c.Name -eq $says) { $asked = $true }
        elseif ($c.Name.StartsWith('chrome-extension://')) {
          $lastMine = $c.Name.StartsWith($mine) -and [bool]($ports | Where-Object { $c.Name -match ('%3A' + $_ + '%2F') })
        }
      } elseif ($c.ControlType -eq $CT::Button -and $c.Name -eq $btnName -and $lastMine) { $target = $e }
    }
    if ($asked -and $target) {
      $ip = $null
      if ($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { $ip.Invoke(); $done = 'clicked' } else { $done = 'no-invoke' }
    }
  }
}
$done
"""


def press_once(run=None, timeout: float = 25.0) -> str:
    """One look at the screen: 'clicked', 'none' (no such page), 'no-invoke' or 'error'. `run(script, env) -> str` is replaceable in tests."""
    env = {"JARVIS_CONNECT_SAYS": CLIENT_SAYS, "JARVIS_CONNECT_BUTTON": BUTTON, "JARVIS_CONNECT_EXT": EXTENSION_ID}
    try:
        out = (run or _powershell)(SCRIPT, env)
    except Exception:                                   # noqa: BLE001 - the kind of failure is not interesting here; the handshake simply times out as before
        return "error"
    last = (out or "").strip().splitlines()[-1:] or ["none"]
    return last[0] if last[0] in ("clicked", "none", "no-invoke") else "error"


def _powershell(script: str, env: dict) -> str:
    import os
    full = dict(os.environ, **env)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=25, env=full,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout


class Approver:
    """A thread that looks for the page every `every` seconds until stopped, `limit_s` runs out, or one click was made."""

    def __init__(self, press=press_once, every: float = 1.5, limit_s: float = 50.0, clock=time.monotonic, sleep=time.sleep):
        self.press, self.every, self.limit_s, self.clock, self.sleep = press, every, limit_s, clock, sleep
        self.stop = threading.Event()
        self.result = "none"
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        end = self.clock() + self.limit_s
        while not self.stop.is_set() and self.clock() < end:
            self.result = self.press()
            if self.result == "clicked":
                return
            self.stop.wait(self.every)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        self._t.join(timeout=30)
