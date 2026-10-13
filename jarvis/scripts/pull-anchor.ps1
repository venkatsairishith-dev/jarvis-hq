# Pull the kernel audit head from the VM and append it to the vault (off-box anchor). Run on the laptop (Windows).
$key  = 'D:\jarvis-secrets\ssh\jarvis_vm_ed25519'
$vm   = 'ubuntu@100.125.91.51'
$note = 'D:\jarvis\vault\decisions\audit-anchors.md'
$a = ssh -i $key -o BatchMode=yes -o ConnectTimeout=15 $vm "cd ~/jarvis/kernel && python3 -m jarvis_kernel audit anchor"
if (-not $a) { throw 'could not read anchor from VM' }
if (-not (Test-Path $note)) { Set-Content $note "# Audit anchors`nOff-box copies of the VM kernel audit head. Verify with: jarvis audit verify --anchor-file <file>`n" -Encoding utf8 }
Add-Content $note ("- {0} {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $a) -Encoding utf8
Set-Location D:\jarvis\vault; git add decisions/audit-anchors.md; git commit -q -m "Audit anchor"; git push -q origin main
"anchor recorded: $a"
