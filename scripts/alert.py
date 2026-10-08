"""Send a Monit alert to the owner's Telegram chat. Monit (root daemon) runs this as the ubuntu user via `exec ... as uid`.
Monit passes the event in environment variables: MONIT_SERVICE, MONIT_EVENT, MONIT_DESCRIPTION, MONIT_HOST.
Usage (from monit): alert.py [fail|ok]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kernel"))
os.environ.setdefault("JARVIS_SECRETS", os.path.expanduser("~/.jarvis/secrets"))

from jarvis_kernel import gateway, telegram  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "fail"
    svc = os.environ.get("MONIT_SERVICE", "?")[:60]
    event = os.environ.get("MONIT_EVENT", "")[:60]
    desc = os.environ.get("MONIT_DESCRIPTION", "")[:200]
    head = "RECOVERED" if kind == "ok" else "ALERT"
    text = f"{head}: {svc} - {event}. {desc}".strip()
    k = Kernel()
    k.audit.append("monit_alert", {"service": svc, "event": event, "kind": kind})
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
