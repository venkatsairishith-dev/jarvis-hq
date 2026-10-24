#!/usr/bin/env bash
# Put Tailscale on the Agent PC VM and keep the agent accounts OFF the owner's tailnet. Run as root ON the VM, after provision_vm2.sh; safe to run again.
#
#   sudo bash provision_vm2_tailscale.sh            install and lock down
#   sudo bash provision_vm2_tailscale.sh --plan     only print what it would do
#
# Install steps are Tailscale's own manual steps for Ubuntu 24.04 (noble) from its documentation (read 2026-10-24): its signing key and apt list from
# pkgs.tailscale.com, then apt-get install. The vendor's one-line installer (a pipe into a shell) is not used. Tailscale publishes no key fingerprint on that
# page, so the script prints the one it got, for the record.
# It does NOT log in: logging in needs the owner. At the end it prints the command; that command prints a login link the owner opens in his own browser.
# Why the lock-down: the agents drive a browser and read untrusted pages. Without it, anything running as an agent account could reach the owner's laptop and
# the main server over the tailnet. The rule below stops the agent accounts from sending anything to tailnet addresses; root (the owner's own SSH login) is not affected.
set -euo pipefail

PLAN=0; [ "${1:-}" = "--plan" ] && PLAN=1
say() { printf '== %s\n' "$*"; }
run() { if [ "$PLAN" = 1 ]; then printf '   would run: %s\n' "$*"; else "$@"; fi; }
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
. /etc/os-release; [ "${VERSION_ID:-}" = "24.04" ] || { echo "Ubuntu 24.04 expected, found ${PRETTY_NAME:-unknown}"; exit 2; }

say "1 Tailscale from its signed apt repository"
if [ "$PLAN" = 1 ]; then
  echo "   would fetch https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg to /usr/share/keyrings/tailscale-archive-keyring.gpg"
  echo "   would fetch https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list to /etc/apt/sources.list.d/tailscale.list, then apt-get install tailscale"
else
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
  grep -q '^deb .*pkgs.tailscale.com' /etc/apt/sources.list.d/tailscale.list || { echo "unexpected repository list: stopping"; exit 3; }
  echo "   signing key fingerprint received: $(gpg --show-keys --with-colons /usr/share/keyrings/tailscale-archive-keyring.gpg | awk -F: '/^fpr:/ {print $10; exit}')"
  DEBIAN_FRONTEND=noninteractive apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale
fi

say "2 the agent accounts may not send anything to the tailnet"
if [ "$PLAN" != 1 ]; then
  cat > /etc/systemd/system/jarvis-agent-firewall.service <<'UNIT'
[Unit]
Description=JARVIS: keep the agent accounts off the tailnet (and Tailscale's own 100.100.100.100)
After=network-pre.target tailscaled.service
[Service]
Type=oneshot
RemainAfterExit=yes
# 100.64.0.0/10 and fd7a:115c:a1e0::/48 are Tailscale's address ranges. Rules are added once (-C checks first).
ExecStart=/bin/sh -c 'for u in agent jarvis-sandbox jarvis-verify; do iptables -C OUTPUT -m owner --uid-owner $u -d 100.64.0.0/10 -j REJECT 2>/dev/null || iptables -I OUTPUT -m owner --uid-owner $u -d 100.64.0.0/10 -j REJECT; ip6tables -C OUTPUT -m owner --uid-owner $u -d fd7a:115c:a1e0::/48 -j REJECT 2>/dev/null || ip6tables -I OUTPUT -m owner --uid-owner $u -d fd7a:115c:a1e0::/48 -j REJECT; done'
[Install]
WantedBy=multi-user.target
UNIT
fi
run systemctl daemon-reload
run systemctl enable --now jarvis-agent-firewall.service
run systemctl enable --now tailscaled

say "3 done; nothing is logged in yet"
echo "The owner joins the VM to his tailnet with this command, run on the VM (it prints a login link for HIS browser; do not paste the link anywhere else):"
echo "   sudo tailscale up --hostname=jarvis-vm2 --shields-up --accept-dns=false --accept-routes=false"
echo "--shields-up: no other tailnet device can open connections to this VM. Afterwards, in the Tailscale admin console, turn key expiry off for this server."
