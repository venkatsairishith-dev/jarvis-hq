# The reliability gate's eyes and hands on Windows (D59). One command per call, ONE line of JSON on stdout.
#   envprobe.ps1 windows                      visible top-level windows with a title: [{hwnd,pid,process,title}]
#   envprobe.ps1 window-text <hwnd>           {text}: the words UI Automation finds in that window (names, values, document text)
#   envprobe.ps1 close <hwnd>                 close ONE window (WM_CLOSE); a "save changes?" box of the same program is answered "Don't save"
#   envprobe.ps1 bin-list                     [{name,from}] what is in the Recycle Bin
#   envprobe.ps1 bin-purge <text>             {removed}: delete from the bin ONLY items whose name contains <text> (never the whole bin)
#   envprobe.ps1 bin-recycle <path>           send one file or folder to the Recycle Bin
#   envprobe.ps1 start <program> [args...]    start a program and return at once
#   envprobe.ps1 msgbox <title> <text>        show a message box in a separate process (for "what does the dialog say" goals)
#   envprobe.ps1 render-image <path> <text>   draw <text> big on a white picture and save it as PNG (for the vision goals)
# D65 (problem_recovery goals: the problem is made on purpose; UNVERIFIED until `--selftest` has run on the laptop)
#   envprobe.ps1 minimize <title-fragment>               minimise the visible windows whose title contains the fragment
#   envprobe.ps1 start-later <seconds> <program> [args]  start a program after a delay (detached)
#   envprobe.ps1 msgbox-later <seconds> <title> <text>   show a message box after a delay (detached)
#   envprobe.ps1 slow-form <title> <marker-path> <secs>  a window whose UI thread does not answer for <secs> seconds, then answers; its OK button writes the marker file
#   envprobe.ps1 close-later <seconds> <title-fragment>  close ONE window whose title contains the fragment after a delay (an app that closes itself)
#   envprobe.ps1 close-title <title-fragment>            (used by close-later)
#   envprobe.ps1 type-unsaved <text>                     start Notepad and type the text, so it has unsaved work
# Written without a Windows machine at hand: `python -m jarvis.evals.reliability.runner --selftest` runs every command once.
param([Parameter(Position = 0)][string]$Cmd, [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class RelWin {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
  public static List<object[]> All() {
    var list = new List<object[]>();
    EnumWindows(delegate (IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      int n = GetWindowTextLength(h);
      if (n <= 0) return true;
      if ((GetWindowLong(h, -20) & 0x80) != 0) return true;           // WS_EX_TOOLWINDOW: not a real window
      var sb = new StringBuilder(n + 1);
      GetWindowText(h, sb, sb.Capacity);
      uint pid; GetWindowThreadProcessId(h, out pid);
      list.Add(new object[] { h.ToInt64(), (int)pid, sb.ToString() });
      return true;
    }, IntPtr.Zero);
    return list;
  }
}
'@

function Out-Json($obj) { [Console]::Out.WriteLine((ConvertTo-Json -InputObject $obj -Compress -Depth 6)) }      # -InputObject keeps a one-item array an array

function Get-Windows {
  foreach ($w in [RelWin]::All()) {
    $name = ''
    try { $name = (Get-Process -Id $w[1] -ErrorAction Stop).ProcessName } catch { }
    [pscustomobject]@{ hwnd = [int64]$w[0]; pid = [int]$w[1]; process = $name; title = [string]$w[2] }
  }
}

function Get-UiaText([int64]$hwnd) {
  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  $parts = New-Object System.Collections.Generic.List[string]
  $parts.Add($root.Current.Name)
  $count = 0
  foreach ($e in $all) {
    $count++
    if ($count -gt 3000) { break }
    try {
      $n = $e.Current.Name
      if ($n) { $parts.Add($n) }
      $vp = $null
      if ($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) { $v = $vp.Current.Value; if ($v) { $parts.Add($v) } }
      $tp = $null
      if ($e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) { $t = $tp.DocumentRange.GetText(20000); if ($t) { $parts.Add($t) } }
    } catch { }
  }
  $text = ($parts -join "`n")
  if ($text.Length -gt 20000) { $text = $text.Substring(0, 20000) }
  $text
}

function Close-One([int64]$hwnd) {
  $h = [IntPtr]$hwnd
  if (-not [RelWin]::IsWindow($h)) { return }
  $procId = [uint32]0
  [void][RelWin]::GetWindowThreadProcessId($h, [ref]$procId)
  [void][RelWin]::SendMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero)      # WM_CLOSE
  Start-Sleep -Milliseconds 700
  # a "save changes?" box of the same program: answer "Don't save"
  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  foreach ($w in [RelWin]::All()) {
    if ([int]$w[1] -ne [int]$procId) { continue }
    try {
      $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][int64]$w[0])
      $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
      foreach ($b in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)) {
        if ($b.Current.Name -match "^Don.t save$|^Don.t Save$|^No$") {
          $ip = $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $ip.Invoke()
          return
        }
      }
    } catch { }
  }
}

function Get-Bin {
  $shell = New-Object -ComObject Shell.Application
  $bin = $shell.NameSpace(10)
  foreach ($it in $bin.Items()) {
    [pscustomobject]@{ name = [string]$it.Name; from = [string]$bin.GetDetailsOf($it, 1); path = [string]$it.Path }
  }
}

switch ($Cmd) {
  'windows' { Out-Json @(Get-Windows) }
  'window-text' { Out-Json ([pscustomobject]@{ text = (Get-UiaText ([int64]$Rest[0])) }) }
  'close' { Close-One ([int64]$Rest[0]); Out-Json ([pscustomobject]@{ ok = $true }) }
  'bin-list' { Out-Json @(Get-Bin | ForEach-Object { [pscustomobject]@{ name = $_.name; from = $_.from } }) }
  'bin-purge' {
    $needle = [string]$Rest[0]
    if ($needle.Length -lt 4) { throw 'bin-purge needs at least 4 characters so that it can never empty the bin' }
    $n = 0
    foreach ($it in @(Get-Bin)) {
      if ($it.name -like "*$needle*") {
        try { Remove-Item -LiteralPath $it.path -Force -Recurse -ErrorAction Stop; $n++ } catch { }
      }
    }
    Out-Json ([pscustomobject]@{ removed = $n })
  }
  'bin-recycle' {
    Add-Type -AssemblyName Microsoft.VisualBasic
    $p = [string]$Rest[0]
    if (Test-Path -LiteralPath $p -PathType Container) {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
    } else {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
    }
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'start' {
    $prog = [string]$Rest[0]
    if ($Rest.Count -gt 1) { Start-Process -FilePath $prog -ArgumentList ($Rest[1..($Rest.Count - 1)]) } else { Start-Process -FilePath $prog }
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'msgbox' {
    $t = ([string]$Rest[0]).Replace("'", "''")
    $m = ([string]$Rest[1]).Replace("'", "''")
    $code = "Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show('$m', '$t')"
    Start-Process -FilePath powershell -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-Command', $code)
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'render-image' {
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap 1100, 360
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::White)
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAlias
    $font = New-Object System.Drawing.Font('Arial', 40, [System.Drawing.FontStyle]::Bold)
    $rect = New-Object System.Drawing.RectangleF 30, 30, 1040, 300
    $g.DrawString([string]$Rest[1], $font, [System.Drawing.Brushes]::Black, $rect)
    $bmp.Save([string]$Rest[0], [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Out-Json ([pscustomobject]@{ ok = $true })
  }

  'minimize' {
    $frag = [string]$Rest[0]
    Add-Type -Namespace RelMin -Name W -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr h, int n);'
    $n = 0
    foreach ($w in @(Get-Windows)) { if ($w.title -like "*$frag*") { [void][RelMin.W]::ShowWindow([IntPtr]$w.hwnd, 6); $n++ } }
    Out-Json ([pscustomobject]@{ minimized = $n })
  }
  'start-later' {
    $secs = [int]$Rest[0]; $prog = [string]$Rest[1]
    $argText = if ($Rest.Count -gt 2) { " -ArgumentList " + (($Rest[2..($Rest.Count - 1)] | ForEach-Object { "'" + ([string]$_).Replace("'", "''") + "'" }) -join ',') } else { '' }
    $code = "Start-Sleep -Seconds $secs; Start-Process -FilePath '" + $prog.Replace("'", "''") + "'" + $argText
    Start-Process -FilePath powershell -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $code)
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'msgbox-later' {
    $secs = [int]$Rest[0]
    $t = ([string]$Rest[1]).Replace("'", "''"); $m = ([string]$Rest[2]).Replace("'", "''")
    $code = "Start-Sleep -Seconds $secs; Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show('$m', '$t')"
    Start-Process -FilePath powershell -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $code)
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'slow-form' {
    $t = ([string]$Rest[0]).Replace("'", "''"); $marker = ([string]$Rest[1]).Replace("'", "''"); $secs = [int]$Rest[2]
    $code = "Add-Type -AssemblyName System.Windows.Forms; `$f = New-Object System.Windows.Forms.Form; `$f.Text = '$t'; `$f.Width = 320; `$f.Height = 160; `$f.StartPosition = 'CenterScreen'; " +
            "`$b = New-Object System.Windows.Forms.Button; `$b.Text = 'OK'; `$b.Name = 'OK'; `$b.Left = 90; `$b.Top = 50; " +
            "`$b.Add_Click({ Set-Content -LiteralPath '$marker' -Value 'pressed'; `$f.Close() }); `$f.Controls.Add(`$b); " +
            "`$f.Add_Shown({ Start-Sleep -Seconds $secs }); [void]`$f.ShowDialog()"
    # A script file, not -Command: the quoting of a long -Command through Start-Process lost the window on Windows 10 (reliability selftest 2026-10-27).
    # The file sits next to the marker (the run's own folder on D:), which the harness removes at the end of the run.
    $file = [System.IO.Path]::ChangeExtension([string]$Rest[1], '.slow.ps1')
    # Not -WindowStyle Hidden: the SW_HIDE of the start-up info is applied to the first window the process shows, which is the form (it never appeared).
    # The script hides its own console instead.
    $hide = @'
Add-Type -Namespace RelHide -Name K -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);'; [void][RelHide.K]::ShowWindow([RelHide.K]::GetConsoleWindow(), 0);
'@
    Set-Content -LiteralPath $file -Value ($hide + ' ' + $code) -Encoding UTF8
    Start-Process -FilePath powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $file + '"'))
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'close-title' {
    $frag = [string]$Rest[0]
    $w = @(Get-Windows | Where-Object { $_.title -like "*$frag*" })[0]
    if ($w) { [void][RelWin]::SendMessage([IntPtr]$w.hwnd, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) }
    Out-Json ([pscustomobject]@{ closed = [bool]$w })
  }
  'close-later' {
    $secs = [int]$Rest[0]; $frag = ([string]$Rest[1]).Replace("'", "''")
    $self = $MyInvocation.MyCommand.Path
    $code = "Start-Sleep -Seconds $secs; & powershell -NoProfile -ExecutionPolicy Bypass -File '$self' close-title '$frag'"
    Start-Process -FilePath powershell -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $code)
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  'type-unsaved' {
    Start-Process -FilePath notepad.exe
    Start-Sleep -Seconds 2
    Add-Type -AssemblyName System.Windows.Forms
    $text = ([string]$Rest[0]) -replace '([\+\^%~\(\)\{\}\[\]])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($text)
    Out-Json ([pscustomobject]@{ ok = $true })
  }
  default { throw "unknown command: $Cmd" }
}
