#!/usr/bin/env bash
# Provision the Agent PC on the rebuilt Oracle VM hermes-agent-vm-2 (Ubuntu 24.04 aarch64). Run as root ON the VM, once; safe to run again.
#
#   sudo bash provision_vm2.sh            do it
#   sudo bash provision_vm2.sh --plan     only print what it would do
#
# What it sets up (docs/reports/CUA_VM2_PLAN_2026-10-24.md): a virtual desktop (XFCE on display :1, VNC on the loopback only), xdotool and xwd,
# Firefox from Mozilla's signed apt repository (key fingerprint checked), the three lab users (agent, jarvis-sandbox, jarvis-verify), the lab
# control and runner services from this folder (root-owned install, as check_lab_deploy.py demands), a firewall that allows only SSH in, and
# unattended security updates. It then runs check_lab_deploy.py and prints the result.
# It does NOT install Tailscale's login (that needs the owner), cua-driver (separate step: its Linux arm64 build must be checked first) or any model key.
set -euo pipefail

PLAN=0; [ "${1:-}" = "--plan" ] && PLAN=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL=/opt/jarvis-hub
MOZ_KEY_FPR="35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3"      # Mozilla's apt signing key; the script stops if the downloaded key differs
say() { printf '== %s\n' "$*"; }
run() { if [ "$PLAN" = 1 ]; then printf '   would run: %s\n' "$*"; else "$@"; fi; }

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
[ "$(uname -m)" = aarch64 ] || say "note: this is $(uname -m), not the Oracle A1 (aarch64); continuing"
. /etc/os-release; [ "${VERSION_ID:-}" = "24.04" ] || { echo "Ubuntu 24.04 expected, found ${PRETTY_NAME:-unknown}"; exit 2; }

say "1 packages (desktop, VNC on loopback, input and screen tools)"
export DEBIAN_FRONTEND=noninteractive
run apt-get update -y
run apt-get install -y --no-install-recommends xfce4 xfce4-terminal dbus-x11 at-spi2-core tigervnc-standalone-server xdotool x11-apps \
    ufw unattended-upgrades python3 ca-certificates curl gnupg fonts-dejavu-core

say "2 Firefox from Mozilla's own apt repository (signed; fingerprint checked)"
if [ "$PLAN" = 1 ]; then echo "   would add /etc/apt/keyrings/packages.mozilla.org.asc, verify $MOZ_KEY_FPR, pin the repo above Ubuntu's snap package"; else
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /tmp/moz.gpg
  got="$(gpg --show-keys --with-colons /tmp/moz.gpg | awk -F: '/^fpr:/ {print $10; exit}')"
  [ "$got" = "$MOZ_KEY_FPR" ] || { echo "Mozilla key fingerprint mismatch ($got): stopping"; exit 3; }
  install -m 0644 /tmp/moz.gpg /etc/apt/keyrings/packages.mozilla.org.asc; rm -f /tmp/moz.gpg
  echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" > /etc/apt/sources.list.d/mozilla.list
  printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > /etc/apt/preferences.d/mozilla
  apt-get update -y && apt-get install -y firefox
fi
run install -d -m 0755 /etc/firefox/policies
[ "$PLAN" = 1 ] || cat > /etc/firefox/policies/policies.json <<'JSON'
{"policies": {"DisableTelemetry": true, "DisableFirefoxStudies": true, "DontCheckDefaultBrowser": true, "OverrideFirstRunPage": "", "OverridePostUpdatePage": "", "DisableAppUpdate": true, "NoDefaultBookmarks": true}}
JSON

say "3 the lab users (no sudo, no shared files)"
for u in agent jarvis-sandbox jarvis-verify; do
  id "$u" >/dev/null 2>&1 || run useradd --create-home --shell /bin/bash --user-group "$u"
  run chmod 0700 "/home/$u"
done

# The take-control socket (lab_control.py) is owned root:hub, so the group hub must exist. `hub` is the account the take-control hub (Caddy) will
# run as later: a system account, no login, no sudo, not the agent. Found missing on the first real run (2026-10-24): lab-control crash-looped.
getent group hub >/dev/null || run groupadd --system hub
id hub >/dev/null 2>&1 || run useradd --system --gid hub --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin hub

say "4 the desktop :1 as the agent user, VNC bound to 127.0.0.1 only"
if [ "$PLAN" != 1 ]; then
  cat > /etc/systemd/system/jarvis-desktop.service <<'UNIT'
[Unit]
Description=JARVIS Agent PC desktop (display :1, VNC on loopback only)
After=network.target
[Service]
User=agent
Environment=HOME=/home/agent
ExecStart=/usr/bin/tigervncserver -fg -localhost yes -SecurityTypes None -geometry 1366x768 -depth 24 -xstartup /usr/bin/startxfce4 :1
Restart=on-failure
NoNewPrivileges=yes
[Install]
WantedBy=multi-user.target
UNIT
fi
# SecurityTypes None is safe ONLY because -localhost yes keeps the port on 127.0.0.1; the owner reaches it through SSH or Tailscale, never directly.

say "5 the lab services, installed root-owned"
run install -d -o root -g root -m 0755 "$INSTALL"
for f in lab_control.py quarantine.py lab_runner.py lab_isolation.py screen_tests.py check_lab_deploy.py; do
  src="$HERE/$f"; [ -f "$src" ] || src="$HERE/../$f"
  [ -f "$src" ] && run install -o root -g root -m 0644 "$src" "$INSTALL/$f" || echo "   missing $f (copy the lab folder next to this script)"
done
for s in jarvis-lab-control.service jarvis-lab-runner.service jarvis-hub-outputs.service; do
  [ -f "$HERE/$s" ] && run install -o root -g root -m 0644 "$HERE/$s" "/etc/systemd/system/$s"
done

say "6 firewall (SSH in only), no rpcbind, unattended security updates"
# Ubuntu listens on port 111 (rpcbind) by default; nothing here needs it, and the firewall is not the only line of defence.
for u in rpcbind.socket rpcbind.service; do systemctl list-unit-files "$u" >/dev/null 2>&1 && run systemctl disable --now "$u" || true; done
run ufw default deny incoming
run ufw default allow outgoing
run ufw allow 22/tcp
run ufw --force enable
run dpkg-reconfigure -f noninteractive unattended-upgrades

say "7 start and check"
run systemctl daemon-reload
run systemctl enable --now jarvis-desktop.service jarvis-lab-control.service
if [ "$PLAN" != 1 ]; then python3 -I "$INSTALL/check_lab_deploy.py" --dir "$INSTALL" || echo "check_lab_deploy reported FAIL lines: fix them before the Agent PC is used"; fi
say "done. Still by hand: join the tailnet (tailscale up, owner logs in), install cua-driver after checking its arm64 build, store the runner token."
