#!/usr/bin/env bash
# OmniRoute 3.8.51 -> 3.8.52 on hermes-agent-vm (owner, 2026-10-26: OmniRoute is the server endpoint). Run ON THE VM as a sudoer.
#   bash omniroute-upgrade.sh            dry run: inspect only, prints what it would do, changes nothing
#   bash omniroute-upgrade.sh --apply    backup, upgrade, restart, verify, roll back by itself on any failed check
#   bash omniroute-upgrade.sh --rollback restore the last backup and restart
# Facts (read 2026-10-26): npm package `omniroute`, latest 3.8.52 (published 2026-10-19), node >=24 ok; the unit is
# jarvis/deploy/jarvis-omniroute.service (user jarvis-router, app in ~/app, env in ~/.omniroute/.env, port 20128, loopback only).
# Never prints the env file or any key. Oracle Always Free only: nothing here creates or resizes a cloud resource.
set -euo pipefail
WANT="${OMNIROUTE_VERSION:-3.8.52}"
USER_R=jarvis-router
HOME_R=/home/$USER_R
APP=$HOME_R/app
DATA=$HOME_R/.omniroute       # the env file (secrets)
DB=$HOME_R/data                # DATA_DIR of the unit: storage.sqlite holds the providers, keys and combos; a newer version may migrate it in place
UNIT=jarvis-omniroute
PORT=20128
BK=/var/backups/jarvis-omniroute
NPM=/opt/node24/bin/npm
MODE="${1:-}"

say() { printf '%s\n' "$*"; }
run_r() { sudo -u "$USER_R" env HOME="$HOME_R" PATH=/opt/node24/bin:/usr/bin:/bin "$@"; }
cur_version() { run_r "$APP/node_modules/.bin/omniroute" --version 2>/dev/null | head -1 || echo unknown; }

inspect() {
  say "== inspect (read only) =="
  say "service: $(systemctl is-active $UNIT || true), enabled: $(systemctl is-enabled $UNIT || true)"
  say "installed version: $(cur_version)"
  say "latest on npm:     $(run_r $NPM view omniroute version 2>/dev/null || echo unreachable)"
  say "memory cap in unit: $(systemctl show $UNIT -p MemoryMax --value)  now: $(systemctl show $UNIT -p MemoryCurrent --value)"
  say "free RAM (MB): $(free -m | awk '/Mem:/ {print $7}')  free disk /home (MB): $(df -m $HOME_R | awk 'NR==2 {print $4}')"
  say "listening sockets for :$PORT (laptop check 2026-10-26: it binds 0.0.0.0; only the INPUT rules below keep it off the internet):"
  ss -ltn "sport = :$PORT" | tail -n +2 || true
  say "firewall rules for :$PORT (each ACCEPT must be on tailscale0, the chain must end in REJECT):"
  sudo iptables -S INPUT 2>/dev/null | grep -E -- "--dport $PORT|REJECT" || say "  (iptables rules not readable)"
  say "disk used by the backup set (MB): app $(sudo du -sm $APP | cut -f1), data $(sudo du -sm $DB | cut -f1)"
  say "unit hardening present: $(grep -c -E 'NoNewPrivileges|ProtectSystem|MemoryMax' /etc/systemd/system/$UNIT.service 2>/dev/null || echo 0) of 3 directives"
}

health() {            # $1 = key-less probe expecting 401/403 on an API route, and the service up on loopback
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Authorization: Bearer wrong-key-on-purpose' "http://127.0.0.1:$PORT/v1/models" || echo 000)
  say "wrong key -> HTTP $code (want 401 or 403)"
  [[ "$code" == 401 || "$code" == 403 ]]
}

public_check() {      # the port must not be open to anything but the tailnet
  # A curl from the VM to its own private address always succeeds (it goes through lo), so that proved nothing and failed a healthy
  # 0.0.0.0 bind. What counts: every INPUT rule that accepts the port is on tailscale0 and the chain ends in REJECT or DROP,
  # or the service listens on loopback / the tailscale address only.
  local binds rules bad=0
  binds=$(ss -ltn "sport = :$PORT" | awk 'NR>1 {print $4}')
  if echo "$binds" | grep -qE '^(0\.0\.0\.0|\*|\[::\]):'; then
    rules=$(sudo iptables -S INPUT 2>/dev/null || true)
    if echo "$rules" | grep -- "--dport $PORT" | grep -v -- "-i tailscale0" | grep -q ACCEPT; then say "port $PORT is accepted on an interface other than tailscale0"; bad=1; fi
    echo "$rules" | grep -qE -- '-A INPUT -j (REJECT|DROP)|^-P INPUT (DROP|REJECT)' || { say "INPUT does not end in REJECT/DROP"; bad=1; }
    echo "$rules" | grep -q -- "-i tailscale0.*--dport $PORT" || say "note: no tailscale0 rule for $PORT"
  fi
  [[ $bad == 0 ]]
}

backup() {
  sudo mkdir -p "$BK" && sudo chmod 700 "$BK"
  local stamp; stamp=$(date -u +%Y%m%dT%H%M%SZ)
  say "backup -> $BK/omniroute-$stamp.tar.gz (contains provider keys: never copy it off this VM, never print it)"
  sudo tar -czf "$BK/omniroute-$stamp.tar.gz" -C "$HOME_R" app .omniroute data
  sudo chmod 600 "$BK/omniroute-$stamp.tar.gz"
  echo "$BK/omniroute-$stamp.tar.gz" | sudo tee "$BK/LAST" >/dev/null
}

rollback() {
  local f; f=$(sudo cat "$BK/LAST")
  say "rolling back from $f"
  sudo systemctl stop $UNIT || true
  sudo rm -rf "$APP"
  sudo tar -xzf "$f" -C "$HOME_R" app
  sudo chown -R $USER_R:$USER_R "$APP"
  # the data folder goes back too: the newer version may have migrated storage.sqlite in place (3.8.52 hashes and encrypts keys on first use)
  sudo tar -xzf "$f" -C "$HOME_R" data .omniroute
  sudo chown -R $USER_R:$USER_R "$DB" "$DATA"
  sudo systemctl start $UNIT
  sleep 5
  health && say "rollback ok, version $(cur_version)"
}

case "$MODE" in
  "" ) inspect; say "dry run only. Next: bash $0 --apply"; exit 0;;
  --rollback ) rollback; exit 0;;
  --apply ) ;;
  * ) say "unknown option $MODE"; exit 2;;
esac

inspect
[[ $(free -m | awk '/Mem:/ {print $7}') -gt 800 ]] || { say "less than 800 MB RAM available: stop"; exit 3; }
[[ $(df -m $HOME_R | awk 'NR==2 {print $4}') -gt 6000 ]] || { say "less than 6 GB disk free (the app is 2.6 GB, the backup about the same): stop"; exit 3; }
if [[ "$(cur_version)" == "$WANT" ]]; then say "already at $WANT (upgraded 2026-10-26 under the owner's standing approval): --apply only backs up and reinstalls the same version, then re-verifies"; fi
backup
say "== upgrade to $WANT =="
sudo systemctl stop $UNIT
if ! run_r $NPM install --prefix "$APP" "omniroute@$WANT" --no-audit --no-fund; then say "npm install failed"; rollback; exit 4; fi
sudo systemctl start $UNIT
sleep 8
say "now running: $(cur_version)"
if ! { health && public_check; }; then say "verification failed"; rollback; exit 5; fi
say "OK: OmniRoute $(cur_version) answers on loopback, a wrong key is refused, the port is accepted on tailscale0 only."
say "Next (manual, nothing printed): enter the Dahl key in OmniRoute's dashboard (http://127.0.0.1:$PORT via the SSH/Tailscale tunnel), create a gateway API key for the laptop, and store it on the laptop in D:\\jarvis-secrets\\providers\\omniroute.key."
