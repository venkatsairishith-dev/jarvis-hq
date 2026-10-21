"""Send a Monit alert to the owner's Telegram chat. Monit (root daemon) runs this as the ubuntu user via `exec ... as uid`.
Monit passes the event in environment variables: MONIT_SERVICE, MONIT_EVENT, MONIT_DESCRIPTION, MONIT_HOST.
Usage (from monit): alert.py [fail|ok]

The kernel audit row (`monit_alert`) is always written first. With Telegram off (config/telegram.off) the alert line goes
to alerts.log in the kernel data folder (~/.jarvis-kernel/alerts.log on the VM): Monit's systemd drop-in
(deploy/monit-override.conf) makes /home read-only for Monit and everything it runs, except that folder. The old place,
~/jarvis/alerts.log, could not be written there.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kernel"))
os.environ.setdefault("JARVIS_SECRETS", os.path.expanduser("~/.jarvis/secrets"))

from jarvis_kernel import telegram  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402


def alerts_log(k) -> str:
    """The fallback log lives next to the kernel's own data: the one folder Monit's sandbox leaves writable."""
    return os.path.join(str(k.dir), "alerts.log")


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "fail"
    svc = os.environ.get("MONIT_SERVICE", "?")[:60]
    event = os.environ.get("MONIT_EVENT", "")[:60]
    desc = os.environ.get("MONIT_DESCRIPTION", "")[:200]
    head = "RECOVERED" if kind == "ok" else "ALERT"
    text = f"{head}: {svc} - {event}. {desc}".strip()
    k = Kernel()
    try:
        return _send(k, svc, event, kind, text)
    finally:
        k.db.close()                              # an in-process caller (tests) must not keep state.db open


def _send(k, svc, event, kind, text):
    k.audit.append("monit_alert",{"service": svc, "event": event, "kind": kind})
    if telegram.disabled():                       # Telegram switched off: the alert stays on the server
        try:
            with open(alerts_log(k), "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
        except OSError as e:                      # the audit row above already holds the alert; say the log failed
            k.audit.append("monit_alert_log_failed", {"service": svc, "error": type(e).__name__})
            return 1
        return 0
    for attempt in range(3):   # the network may be the thing that is failing: try a few times
        try:
            telegram.from_files(k, []).say(text)
            return 0
        except Exception:
            time.sleep(5 * (attempt + 1))
    k.audit.append("monit_alert_undelivered", {"service": svc})
    return 1


if __name__ == "__main__":
    sys.exit(main())
