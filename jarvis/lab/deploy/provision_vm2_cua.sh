#!/usr/bin/env bash
# Install cua-driver (the computer-use driver) on the Agent PC VM, pinned and checked, with telemetry OFF before it ever runs. Run as root ON the VM; safe to run again.
#
#   sudo bash provision_vm2_cua.sh            do it
#   sudo bash provision_vm2_cua.sh --plan     only print what it would do
#
# Facts (read 2026-10-24 from the vendor's release page and docs, and from the binary's own strings): release trycua/cua `cua-driver-rs-v0.34.0` has a Linux arm64
# build (labelled "preview" by the vendor); telemetry is ON by default and goes to PostHog (eu.i.posthog.com); `cua-driver telemetry disable` turns it off for good;
# `serve` listens on a local socket only; MCP uses stdio. The vendor's installer is a pipe from curl into a shell: NOT used. The file is downloaded, its SHA-256 must
# equal the hash pinned below AND the vendor's SHA256SUMS for the same release, then it is unpacked into a root-owned folder. Order matters: the telemetry hosts are
# blocked in /etc/hosts first, then `telemetry disable` runs as the agent user, and only then does anything else run. The service is installed but NOT started or enabled:
# the owner starts it after reading `cua-driver doctor`.
set -euo pipefail

PLAN=0; [ "${1:-}" = "--plan" ] && PLAN=1
VERSION=0.34.0
BASE="https://github.com/trycua/cua/releases/download/cua-driver-rs-v${VERSION}"
FILE="cua-driver-rs-${VERSION}-linux-arm64-binary.tar.gz"
PIN_SHA256="9db8b9084add57eb97be8164367b24b6be54ed4f3dc01213e64b72d7fc09fddb"
DEST="/opt/cua-driver/${VERSION}"
say() { printf '== %s\n' "$*"; }
run() { if [ "$PLAN" = 1 ]; then printf '   would run: %s\n' "$*"; else "$@"; fi; }

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
[ "$(uname -m)" = aarch64 ] || { echo "this build is for aarch64, found $(uname -m)"; exit 2; }
. /etc/os-release; [ "${VERSION_ID:-}" = "24.04" ] || { echo "Ubuntu 24.04 expected, found ${PRETTY_NAME:-unknown}"; exit 2; }
id agent >/dev/null 2>&1 || { echo "run provision_vm2.sh first (the agent user is missing)"; exit 2; }

say "1 telemetry and update hosts blocked first (a null route in /etc/hosts), before the program exists on this machine"
if [ "$PLAN" = 1 ]; then echo "   would add a marked block to /etc/hosts: 0.0.0.0 eu.i.posthog.com us.i.posthog.com us-assets.i.posthog.com eu-assets.i.posthog.com app.posthog.com"; else
  grep -q '# jarvis-cua-telemetry-block' /etc/hosts || cat >> /etc/hosts <<'HOSTS'
# jarvis-cua-telemetry-block
0.0.0.0 eu.i.posthog.com us.i.posthog.com eu-assets.i.posthog.com us-assets.i.posthog.com app.posthog.com
# end jarvis-cua-telemetry-block
HOSTS
fi

say "2 libraries the vendor lists for a minimal Ubuntu image"
export DEBIAN_FRONTEND=noninteractive
run apt-get install -y --no-install-recommends libxi6 at-spi2-core

say "3 download, check against the pinned hash AND the vendor's SHA256SUMS, unpack root-owned"
if [ "$PLAN" = 1 ]; then echo "   would download $BASE/$FILE and $BASE/SHA256SUMS, require sha256 $PIN_SHA256 in both, unpack to $DEST (root:root), link /usr/local/bin/cua-driver"; else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$BASE/$FILE" -o "$tmp/$FILE"
  curl -fsSL "$BASE/SHA256SUMS" -o "$tmp/SHA256SUMS"
  got="$(sha256sum "$tmp/$FILE" | cut -d' ' -f1)"
  [ "$got" = "$PIN_SHA256" ] || { echo "hash differs from the pinned one ($got): stopping"; exit 3; }
  grep -q "^$PIN_SHA256  $FILE\$" "$tmp/SHA256SUMS" || { echo "the vendor's SHA256SUMS does not list this hash for $FILE: stopping"; exit 3; }
  install -d -o root -g root -m 0755 /opt/cua-driver "$DEST"
  tar -xzf "$tmp/$FILE" -C "$DEST" --no-same-owner
  chown -R root:root "$DEST"; chmod -R go-w "$DEST"
  ln -sfn "$DEST" /opt/cua-driver/current
  ln -sfn /opt/cua-driver/current/cua-driver /usr/local/bin/cua-driver
fi

say "4 telemetry OFF, as the agent user, before anything else runs; it must read back as disabled"
if [ "$PLAN" = 1 ]; then echo "   would run as agent: cua-driver telemetry disable ; cua-driver telemetry status (must say disabled, else stop)"; else
  sudo -u agent -H env -i HOME=/home/agent PATH=/usr/bin:/bin /usr/local/bin/cua-driver telemetry disable
  st="$(sudo -u agent -H env -i HOME=/home/agent PATH=/usr/bin:/bin /usr/local/bin/cua-driver telemetry status 2>&1 || true)"
  echo "   telemetry status: $st"
  echo "$st" | grep -qi 'disabled' || { echo "telemetry is not shown as disabled: stopping, nothing else was run"; exit 3; }
fi

say "5 the service: installed, NOT enabled, NOT started"
if [ "$PLAN" != 1 ]; then
  cat > /etc/systemd/system/cua-driver.service <<'UNIT'
[Unit]
Description=cua-driver (computer-use driver) for the JARVIS Agent PC, local socket only
After=jarvis-desktop.service
Requires=jarvis-desktop.service
[Service]
User=agent
Environment=HOME=/home/agent DISPLAY=:1
ExecStartPre=/usr/local/bin/cua-driver telemetry disable
ExecStart=/usr/local/bin/cua-driver serve --permission-mode standard --no-overlay
Restart=on-failure
NoNewPrivileges=yes
PrivateTmp=no
ProtectSystem=strict
ReadWritePaths=/home/agent
[Install]
WantedBy=multi-user.target
# the owner starts it by hand after reading `cua-driver doctor`: systemctl enable --now cua-driver
UNIT
  systemctl daemon-reload
fi

say "6 what the driver says about this machine (read-only; run as agent on display :1)"
if [ "$PLAN" = 1 ]; then echo "   would run as agent: DISPLAY=:1 cua-driver doctor"; else
  sudo -u agent -H env -i HOME=/home/agent PATH=/usr/bin:/bin DISPLAY=:1 /usr/local/bin/cua-driver doctor 2>&1 | head -40 || true
fi
say "done. Next, by the owner: read the doctor output, then enable the cua-driver service yourself. Keep an eye on egress (telemetry stays blocked and disabled)."
