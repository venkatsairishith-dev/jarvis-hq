"""What this Windows PC is playing right now, from Windows' own media sessions (the ones the volume flyout shows):
app, title, artist and Playing / Paused. Read-only. Used to check that "play a song" really started playing. Windows 10+.
"""
import json

from . import runtime

SMTC = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); [void]$task.Wait(5000); $task.Result }
[void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$out = @()
foreach ($s in $mgr.GetSessions()) {
  $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
  $tl = $s.GetTimelineProperties()
  $out += [pscustomobject]@{ app = $s.SourceAppUserModelId; title = $p.Title; artist = $p.Artist;
                             status = $s.GetPlaybackInfo().PlaybackStatus.ToString(); position_s = [math]::Round($tl.Position.TotalSeconds, 1) }
}
@($out) | ConvertTo-Json -Compress
"""


def state(p: dict = None, runner=None) -> dict:
    runner = runner or runtime.powershell
    r = runner(SMTC, {}, 30)
    if r.returncode != 0:
        return {"ok": False, "error": "Windows media sessions could not be read"}
    try:
        text = (r.stdout or "").strip()
        rows = json.loads(text) if text else []
        rows = rows if isinstance(rows, list) else [rows]
    except ValueError:
        return {"ok": False, "error": "unreadable media state"}
    sessions = [{k: str(s.get(k) or "")[:200] if k != "position_s" else s.get(k) for k in ("app", "title", "artist", "status", "position_s")}
                for s in rows if isinstance(s, dict)]
    return {"ok": True, "sessions": sessions, "playing": [s for s in sessions if s["status"] == "Playing"]}
