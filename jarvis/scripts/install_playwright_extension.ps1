# Windows. Installs Microsoft's Playwright extension (Chrome Web Store id mmlmfjhmonkocbjadbfplnigmagldckm, the id
# @playwright/mcp itself expects) into ONE existing Chrome profile, through Chrome's own Web Store page and its own
# "Add extension" confirmation, by accessible names (UI Automation), never by screen position. Nothing else is changed.
#   install_playwright_extension.ps1 -Profile "Profile 1"
param([Parameter(Mandatory = $true)][ValidatePattern('^(Default|Profile \d+)$')][string]$Profile, [int]$TimeoutS = 45)
$ErrorActionPreference = 'Stop'
$id = 'mmlmfjhmonkocbjadbfplnigmagldckm'
$userData = Join-Path $env:LOCALAPPDATA 'Google\Chrome\User Data'
$extDir = Join-Path $userData "$Profile\Extensions\$id"
if (Test-Path $extDir) { "already installed in $Profile"; exit 0 }
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]
function Find-Named($root, [string[]]$names, [string]$type) {
  foreach ($n in $names) {
    $c = New-Object System.Windows.Automation.AndCondition(
      (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, $n)),
      (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::$type)))
    $e = $root.FindFirst($Scope::Descendants, $c)
    if ($e) { return $e }
  }
  return $null
}
function Chrome-Windows { $A::RootElement.FindAll($Scope::Children, (New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'Chrome_WidgetWin_1'))) }
function Press($e) {
  $p = $null
  if ($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) { $p.Invoke() } else { throw "no Invoke on '$($e.Current.Name)'" }
}
function Close-StoreTabs {
  # only the extension's own store tabs (opened by this setup), each by its own close button: one store tab at a time
  # means the buttons below can only belong to the profile being set up
  foreach ($w in Chrome-Windows) {
    $tabs = $w.FindAll($Scope::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)))
    foreach ($t in $tabs) {
      if ($t.Current.Name -notlike 'Playwright Extension - Chrome Web Store*') { continue }
      $x = Find-Named $t @('Close') 'Button'
      if ($x) { try { Press $x; Start-Sleep -Milliseconds 400 } catch { } }
    }
  }
}
Close-StoreTabs
Start-Sleep 1
Start-Process $chrome -ArgumentList "--profile-directory=`"$Profile`"", "https://chromewebstore.google.com/detail/playwright-extension/$id"
$deadline = (Get-Date).AddSeconds($TimeoutS)
$added = $false
while ((Get-Date) -lt $deadline -and -not $added) {
  Start-Sleep -Milliseconds 1500
  foreach ($w in Chrome-Windows) {
    if ($w.Current.Name -notlike '*Chrome Web Store*') { continue }
    $tip = Find-Named $w @('Got it') 'Button'      # a Chrome feature-tip bubble hides the page from UI Automation until dismissed
    if ($tip) { try { Press $tip; "dismissed a Chrome tip bubble" } catch { } ; Start-Sleep 2 }
    $done = Find-Named $w @('Remove from Chrome') 'Button'
    if ($done) { "the store says it is already added in this window"; $added = $true; break }
    $b = Find-Named $w @('Add to Chrome') 'Button'
    if ($b -and $b.Current.IsEnabled) { Press $b; "pressed: Add to Chrome"; $added = $true; break }   # disabled while the page loads
  }
}
if (-not $added) { throw "the 'Add to Chrome' button did not appear within $TimeoutS s" }
# Chrome's own confirmation ("Add "Playwright Extension"?") is a separate small window or a child of the browser window
$deadline = (Get-Date).AddSeconds(20)
$confirmed = $false
while ((Get-Date) -lt $deadline -and -not $confirmed -and -not (Test-Path $extDir)) {
  Start-Sleep -Milliseconds 800
  foreach ($w in Chrome-Windows) {
    $b = Find-Named $w @('Add extension') 'Button'
    if ($b) { Press $b; "pressed: Add extension"; $confirmed = $true; break }
  }
}
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and -not (Test-Path $extDir)) { Start-Sleep -Milliseconds 800 }
Start-Sleep 1
Close-StoreTabs
if (Test-Path $extDir) { "installed in $Profile (folder present: $((Get-ChildItem $extDir -Directory | Select-Object -First 1).Name))" } else { throw "not installed in $Profile (no extension folder after confirming)" }
