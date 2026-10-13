"""Compare the built-in scrubber with gitleaks on made-up secrets in real formats (all values are random, not real)."""
import json
import os
import random
import string
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kernel"))
from jarvis_kernel.scrub import scrub

GL = os.environ.get("GITLEAKS", r"D:\jarvis\tools\gitleaks\bin\gitleaks.exe")
rnd = random.Random(7)
def r(n, alpha=string.ascii_letters + string.digits):
    return "".join(rnd.choice(alpha) for _ in range(n))

CASES = {
    "aws_access_key": "aws_access_key_id = AKIA" + r(16, string.ascii_uppercase + string.digits),
    "github_pat": "token ghp_" + r(36),
    "github_fine_grained": "GITHUB_TOKEN=github_pat_" + r(22) + "_" + r(59),
    "slack_bot_token": "xoxb-" + r(11, string.digits) + "-" + r(12, string.digits) + "-" + r(24),
    "google_api_key": "key=AIza" + r(35, string.ascii_letters + string.digits + "-_"),
    "stripe_live": "sk_live_" + r(24),
    "groq_key": "GROQ_API_KEY=gsk_" + r(52),
    "openrouter_key": "sk-or-v1-" + r(64, string.hexdigits.lower()),
    "telegram_bot_token": "bot token 1234567890:AA" + r(33, string.ascii_letters + string.digits + "-_"),
    "jwt": "eyJ" + r(20) + "." + "eyJ" + r(30) + "." + r(43, string.ascii_letters + string.digits + "-_"),
    "generic_password_assignment": 'db_password = "' + r(16, string.ascii_letters + string.digits + "!#$%") + '"',
    "npm_token": "//registry.npmjs.org/:_authToken=npm_" + r(36),
    "sendgrid": "SG." + r(22, string.ascii_letters + string.digits + "-_") + "." + r(43, string.ascii_letters + string.digits + "-_"),
    "private_key_header": "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(r(64, string.ascii_letters + string.digits + "+/") for _ in range(4)) + "\n-----END RSA PRIVATE KEY-----",
    "plain_text_no_secret": "The quarterly report shows revenue up four percent; run pip install fastembed.",
}


def gitleaks_findings(text):
    with tempfile.TemporaryDirectory() as d:
        rep = os.path.join(d, "r.json")
        p = subprocess.run([GL, "stdin", "--no-banner", "--report-format", "json", "--report-path", rep, "--exit-code", "0"],
                           input=text, capture_output=True, text=True, timeout=30, creationflags=0x08000000 if os.name == "nt" else 0)
        try:
            return json.load(open(rep)) or []
        except (OSError, ValueError):
            return None


def main():
    print(f"{'case':30s} {'built-in scrub':18s} gitleaks")
    mine = theirs = 0
    for name, text in CASES.items():
        clean, findings, blocked = scrub(text)
        hit_mine = blocked or bool(findings)
        gl = gitleaks_findings(text)
        hit_gl = bool(gl)
        if name != "plain_text_no_secret":
            mine += hit_mine; theirs += hit_gl
        print(f"{name:30s} {'caught' if hit_mine else 'MISSED':18s} {('caught: ' + ','.join(sorted({f['RuleID'] for f in gl}))) if hit_gl else ('MISSED' if gl is not None else 'error')}")
    n = len(CASES) - 1
    print(f"\nbuilt-in caught {mine}/{n}; gitleaks caught {theirs}/{n}")


if __name__ == "__main__":
    main()
