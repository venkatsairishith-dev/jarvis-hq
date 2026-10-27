#!/usr/bin/env bash
# VM2 as a dedicated, persistent Linux desktop the owner can watch and take over (D79, 2026-10-27). Run as root ON VM2, by the owner or the laptop session with the owner's yes.
# NOT run by the cloud session: this file only exists in the repository. Safe to run again.
#
#   sudo bash provision_vm2_desktop.sh --plan       only print what it would do
#   sudo bash provision_vm2_desktop.sh              do it
#   sudo bash provision_vm2_desktop.sh --viewer     also install the takeover viewer (noVNC + websockify from Ubuntu's apt); units are installed, NOT started
#
# What it does (docs/03_DECISIONS.md D79, jarvis/lab/deploy/VM2_DESKTOP.md):
#   1  a PERSISTENT Firefox profile for the unprivileged agent user in /home/agent/.jarvis-profile/firefox: the owner logs in ONCE himself through the takeover viewer and the
#      sessions stay across restarts; JARVIS never types a password there and never reads the profile. `jarvis-vm2-profile reset` starts a fresh one (the old one is moved
#      aside, not deleted).
#   2  the DOWNLOAD QUARANTINE /home/agent/quarantine (0700): Firefox saves every download there and asks nothing; lab/quarantine.py (inside lab_control) inspects it.
#   3  Firefox policies (telemetry off as before, plus the download folder). Firefox is started through /usr/local/bin/jarvis-firefox so every window uses the one profile.
#   4  (--viewer) noVNC + websockify on the Tailscale address only; the VNC password is set by the owner with `vncpasswd` and typed by him in the viewer, never in an address.
# It does NOT touch the agent user's rights (no sudo, no groups), the firewall's default policy, the egress allow-list (a server step), tokens or keys.
set -euo pipefail

PLAN=0; VIEWER=0
for a in "$@"; do case "$a" in --plan) PLAN=1;; --viewer) VIEWER=1;; esac; done
AGENT=agent
PROFILE=/home/$AGENT/.jarvis-profile/firefox
QUARANTINE=/home/$AGENT/quarantine
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '== %s\n' "$*"; }
run() { if [ "$PLAN" = 1 ]; then printf '   would run: %s\n' "$*"; else "$@"; fi; }

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
id "$AGENT" >/dev/null 2>&1 || { echo "run provision_vm2.sh first (the agent user is missing)"; exit 2; }

say "1 persistent Firefox profile (owner logs in once through the viewer; sessions survive restarts)"
run install -d -o "$AGENT" -g "$AGENT" -m 0700 "/home/$AGENT/.jarvis-profile" "$PROFILE"
if [ "$PLAN" = 1 ]; then echo "   would write /usr/local/bin/jarvis-firefox (exec firefox --no-remote --profile $PROFILE) and /usr/local/sbin/jarvis-vm2-profile (status | reset)"; else
  cat > /usr/local/bin/jarvis-firefox <<'SH'
#!/bin/sh
# Every Firefox window of the agent desktop uses the one persistent profile. No password is ever passed here.
exec /usr/bin/firefox --no-remote --profile /home/agent/.jarvis-profile/firefox "$@"
SH
  chmod 0755 /usr/local/bin/jarvis-firefox
  cat > /usr/local/sbin/jarvis-vm2-profile <<'SH'
#!/bin/sh
# jarvis-vm2-profile status | reset      (root). `reset` moves the browser profile aside and starts a fresh, empty one: every site is logged out and the owner logs in again
# himself through the takeover viewer. The old profile is KEPT (renamed with the date); delete it yourself when you are sure: rm -r /home/agent/.jarvis-profile/firefox.old-DATE
P=/home/agent/.jarvis-profile/firefox
case "$1" in
  status) [ -d "$P" ] && du -sh "$P" | cut -f1 | sed 's/^/profile size: /' || echo "no profile yet"; ls -d "$P".old-* 2>/dev/null | sed 's/^/kept: /';;
  reset)
    [ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
    pkill -u agent -x firefox 2>/dev/null || true; pkill -u agent -x firefox-bin 2>/dev/null || true; sleep 1
    if [ -d "$P" ]; then mv "$P" "$P.old-$(date +%Y%m%d-%H%M%S)"; fi
    install -d -o agent -g agent -m 0700 "$P"
    echo "fresh profile created; the old one was kept next to it";;
  *) echo "usage: jarvis-vm2-profile status|reset"; exit 2;;
esac
SH
  chmod 0755 /usr/local/sbin/jarvis-vm2-profile
  cat > /usr/share/applications/jarvis-firefox.desktop <<'DESK'
[Desktop Entry]
Name=Firefox (JARVIS profile)
Exec=/usr/local/bin/jarvis-firefox %u
Terminal=false
Type=Application
Categories=Network;WebBrowser;
MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;
DESK
  sudo -u "$AGENT" -H env -i HOME=/home/$AGENT PATH=/usr/bin:/bin xdg-settings set default-web-browser jarvis-firefox.desktop 2>/dev/null || echo "   (default browser not set from here: set it once in Settings > Default Applications on the desktop)"
fi

say "2 the download quarantine (Firefox saves here without asking; nothing leaves VM2 without the owner's click on the laptop)"
run install -d -o "$AGENT" -g "$AGENT" -m 0700 "$QUARANTINE"

say "3 Firefox policies: telemetry off as before, downloads go to the quarantine"
run install -d -m 0755 /etc/firefox/policies
if [ "$PLAN" = 1 ]; then echo "   would write /etc/firefox/policies/policies.json with DownloadDirectory=$QUARANTINE and PromptForDownloadLocation=false"; else
  cat > /etc/firefox/policies/policies.json <<JSON
{"policies": {"DisableTelemetry": true, "DisableFirefoxStudies": true, "DontCheckDefaultBrowser": true, "OverrideFirstRunPage": "", "OverridePostUpdatePage": "",
 "DisableAppUpdate": true, "NoDefaultBookmarks": true, "DownloadDirectory": "$QUARANTINE", "DefaultDownloadDirectory": "$QUARANTINE", "PromptForDownloadLocation": false,
 "PasswordManagerEnabled": false, "OfferToSaveLogins": false}}
JSON
fi
# PasswordManagerEnabled=false: Firefox does not offer to save a password. The owner's sessions are cookies kept in the profile; no password is stored by JARVIS or by the browser.

say "4 lab_control gets the quarantine inspector (root-owned, next to it)"
if [ -f "$HERE/../quarantine.py" ] || [ -f "$HERE/quarantine.py" ]; then
  src="$HERE/../quarantine.py"; [ -f "$src" ] || src="$HERE/quarantine.py"
  run install -o root -g root -m 0644 "$src" /opt/jarvis-hub/quarantine.py
  run install -o root -g root -m 0644 "$HERE/../lab_control.py" /opt/jarvis-hub/lab_control.py
  run install -o root -g root -m 0644 "$HERE/../jarvis_mcp.py" /home/$AGENT/lab/jarvis_mcp.py 2>/dev/null || echo "   (jarvis_mcp.py: copy it to the agent's tool-server folder yourself; its path differs per install)"
  run systemctl restart jarvis-lab-control.service
else
  echo "   quarantine.py not found next to this script: copy the lab folder"
fi

if [ "$VIEWER" = 1 ]; then
  say "5 the takeover viewer: noVNC + websockify on the Tailscale address only (units installed, NOT started)"
  export DEBIAN_FRONTEND=noninteractive
  run apt-get install -y --no-install-recommends novnc websockify
  TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  [ -n "$TS_IP" ] || { echo "   tailscale has no address yet: join the tailnet first (provision_vm2_tailscale.sh), then run --viewer again"; exit 3; }
  if [ "$PLAN" = 1 ]; then echo "   would install jarvis-vm2-viewer.service listening on $TS_IP:6080 only, and a vncauth drop-in for jarvis-desktop.service"; else
    sed "s/@TS_IP@/$TS_IP/g" "$HERE/jarvis-vm2-viewer.service" > /etc/systemd/system/jarvis-vm2-viewer.service
    chmod 0644 /etc/systemd/system/jarvis-vm2-viewer.service
    systemctl daemon-reload
  fi
  cat <<EOF
   OWNER, by hand (JARVIS never sees this password):
     1. sudo -u $AGENT vncpasswd            (type the viewer password twice; it is stored hashed in /home/$AGENT/.vnc/passwd)
     2. add the drop-in jarvis-desktop.service.d/vncauth.conf (see VM2_DESKTOP.md) so the desktop asks for that password, then: systemctl restart jarvis-desktop
     3. systemctl enable --now jarvis-vm2-viewer
     4. put http://$TS_IP:6080/vnc.html?autoconnect=1&resize=scale in jarvis/config/vm2.json as viewer_url (no password in it) and set enabled to true
EOF
fi
say "done. Next: VM2_DESKTOP.md (the owner's steps), then python3 -I /opt/jarvis-hub/check_lab_deploy.py"
