# Windows. The owner's manual approval of ONE Playwright extension connection, done by UI Automation on the owner's
# instruction: on the extension's own connect page, press "Allow & select" for a tab whose address starts with -TabUrl
# (a harmless page opened for this, e.g. https://example.com/). Rows are flat on that page: a button's tab is the title
# and address right before it. Only those two names are read; nothing else on the page (it also shows a token) is read.
param([string]$TabUrl = 'https://example.com/', [int]$TimeoutS = 60)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]; $S = [System.Windows.Automation.TreeScope]
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$btnCond = New-Object System.Windows.Automation.AndCondition(
  (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, 'Allow & select')),
  (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)))
$deadline = (Get-Date).AddSeconds($TimeoutS)
while ((Get-Date) -lt $deadline) {
  foreach ($w in $A::RootElement.FindAll($S::Children, (New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'Chrome_WidgetWin_1')))) {
    foreach ($b in $w.FindAll($S::Descendants, $btnCond)) {
      $url = $walker.GetPreviousSibling($b)
      if ($url -and $url.Current.Name.StartsWith($TabUrl)) {
        ($b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
        "allowed: a tab at $TabUrl"
        exit 0
      }
    }
  }
  Start-Sleep -Milliseconds 700
}
"no connect prompt with a tab at $TabUrl within $TimeoutS s"
exit 1
