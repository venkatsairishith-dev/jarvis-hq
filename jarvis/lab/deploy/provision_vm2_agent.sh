#!/usr/bin/env bash
# Install the lab agent (Hermes Agent) and its model forwarder on the Agent PC VM, pinned and checked. Run as root ON the VM; safe to run again.
#
#   sudo bash provision_vm2_agent.sh            do it
#   sudo bash provision_vm2_agent.sh --plan     only print what it would do
#
# Facts (read 2026-10-25): the lab's runner starts `~/.local/bin/hermes chat -q ... --oneshot` as the agent user and, for the independent check, as jarvis-verify
# (lab_runner.py `command("hermes", ...)`); Hermes Agent v0.21.6 (NousResearch, MIT) is the adopted runtime (docs/01_RESEARCH_AUDIT.md). The tag v0.21.6 resolves to
# commit 818c13be1dc4fd28987e1e881a9408224afd4535 (GitHub API, today). The main server runs the same commit from its own `uv.lock` in a Python 3.14.7 venv, with three
# packages raised to patched releases: httpx2 2.12.0, httpcore2 2.12.0, PyJWT 2.14.0; their wheel hashes below are the main server's own files AND PyPI's published
# hashes (equal). uv 0.13.5 (astral-sh/uv, released 2026-10-22) aarch64 tarball: the hash below equals the vendor's `.sha256` file. Nothing is piped into a shell.
# Models: NO key on this VM. The agent talks to http://127.0.0.1:18790/v1 (a dummy key); model_forwarder.py (root, loopback only) adds the kernel token from
# /etc/jarvis-lab/model.token (root 600) and forwards only the model paths to the kernel's endpoint over the tailnet. The token is installed by the owner's hand
# (scp by path), never by this script. Tools for the lab agent match the lab design: terminal, file, code execution, web and delegation; everything else off.
set -euo pipefail

PLAN=0; [ "${1:-}" = "--plan" ] && PLAN=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV_VERSION=0.13.5
UV_FILE="uv-aarch64-unknown-linux-gnu.tar.gz"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_FILE}"
UV_SHA256="4bb69cf95ed13f23713bb7bc09f241896ea0c75b7c557e615a20604b82f9c403"
PY_VERSION=3.14.7
HERMES_URL="https://github.com/NousResearch/hermes-agent.git"
HERMES_COMMIT="818c13be1dc4fd28987e1e881a9408224afd4535"      # tag v0.21.6
OPT=/opt/hermes-agent
FWD_PORT=18790
say() { printf '== %s\n' "$*"; }
run() { if [ "$PLAN" = 1 ]; then printf '   would run: %s\n' "$*"; else "$@"; fi; }

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
[ "$(uname -m)" = aarch64 ] || { echo "this script is for aarch64, found $(uname -m)"; exit 2; }
. /etc/os-release; [ "${VERSION_ID:-}" = "24.04" ] || { echo "Ubuntu 24.04 expected, found ${PRETTY_NAME:-unknown}"; exit 2; }
for u in agent jarvis-verify; do id "$u" >/dev/null 2>&1 || { echo "run provision_vm2.sh first ($u is missing)"; exit 2; }; done

say "1 tools the build needs (from Ubuntu)"
export DEBIAN_FRONTEND=noninteractive
run apt-get install -y --no-install-recommends git ca-certificates curl

say "2 uv ${UV_VERSION}: download, SHA-256 must equal the pin AND the vendor's .sha256 file, install root-owned"
if [ "$PLAN" = 1 ]; then echo "   would download $UV_URL and $UV_URL.sha256, require sha256 $UV_SHA256 in both, unpack to /opt/uv/${UV_VERSION} (root:root), link /usr/local/bin/uv"; else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$UV_URL" -o "$tmp/$UV_FILE"
  curl -fsSL "$UV_URL.sha256" -o "$tmp/$UV_FILE.sha256"
  got="$(sha256sum "$tmp/$UV_FILE" | cut -d' ' -f1)"
  [ "$got" = "$UV_SHA256" ] || { echo "uv hash differs from the pinned one ($got): stopping"; exit 3; }
  grep -q "^$UV_SHA256 " "$tmp/$UV_FILE.sha256" || { echo "the vendor's .sha256 does not list the pinned hash: stopping"; exit 3; }
  install -d -o root -g root -m 0755 /opt/uv "/opt/uv/${UV_VERSION}"
  tar -xzf "$tmp/$UV_FILE" -C "/opt/uv/${UV_VERSION}" --strip-components=1 --no-same-owner
  chown -R root:root "/opt/uv/${UV_VERSION}"; chmod -R go-w "/opt/uv/${UV_VERSION}"
  ln -sfn "/opt/uv/${UV_VERSION}/uv" /usr/local/bin/uv
fi

say "3 Hermes Agent source at the pinned commit (root-owned; the agent cannot change it)"
if [ "$PLAN" = 1 ]; then echo "   would run: git clone $HERMES_URL $OPT/src ; git checkout --detach $HERMES_COMMIT ; require git rev-parse HEAD = $HERMES_COMMIT ; chown -R root:root $OPT"; else
  install -d -o root -g root -m 0755 "$OPT"
  [ -d "$OPT/src/.git" ] || git clone --no-checkout "$HERMES_URL" "$OPT/src"
  git -C "$OPT/src" -c advice.detachedHead=false checkout --detach "$HERMES_COMMIT"
  [ "$(git -C "$OPT/src" rev-parse HEAD)" = "$HERMES_COMMIT" ] || { echo "commit differs from the pin: stopping"; exit 3; }
  [ -z "$(git -C "$OPT/src" status --porcelain)" ] || { echo "source tree is not clean: stopping"; exit 3; }
  chown -R root:root "$OPT"; chmod -R go-w "$OPT"
fi

say "4 Python ${PY_VERSION} (uv's own managed build) and the upstream uv.lock, hash-checked by uv, no source builds"
if [ "$PLAN" = 1 ]; then
  echo "   would run: UV_PYTHON_INSTALL_DIR=/opt/uv/python uv python install ${PY_VERSION}"
  echo "   would run: UV_PROJECT_ENVIRONMENT=$OPT/venv uv sync --frozen --no-dev --no-editable --python ${PY_VERSION} --no-build  (in $OPT/src; fails rather than building anything from source)"
else
  export UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_CACHE_DIR=/var/cache/jarvis-uv UV_PYTHON_DOWNLOADS=manual
  /usr/local/bin/uv python install "${PY_VERSION}"
  ( cd "$OPT/src" && UV_PROJECT_ENVIRONMENT="$OPT/venv" /usr/local/bin/uv sync --frozen --no-dev --no-editable --python "${PY_VERSION}" --no-build )
  chown -R root:root /opt/uv/python "$OPT"; chmod -R go-w /opt/uv/python "$OPT"
fi

say "5 the three patched releases the main server runs (httpx2, httpcore2, PyJWT), hash-pinned"
if [ "$PLAN" = 1 ]; then echo "   would run: uv pip install --python $OPT/venv/bin/python --require-hashes -r (httpx2==2.12.0 cc8b6eec..., httpcore2==2.12.0 7e04258c..., pyjwt==2.14.0 ad0cef71...)"; else
  req="$(mktemp)"
  cat > "$req" <<'REQ'
httpx2==2.12.0 --hash=sha256:cc8b6eecb8661c146b8f89a60e97456ee086e91a784ed31ac450c3a9e613dd36
httpcore2==2.12.0 --hash=sha256:7e04258ce01013d7d615e5b910a3b27fac937d7a95038227e79652b4ba3b4ceb
pyjwt==2.14.0 --hash=sha256:ad0cef71c756a56e74863c2919cf0985f72decbcfcb550ee2f422e7c62b5eedc
REQ
  chmod -R u+w "$OPT/venv"
  UV_CACHE_DIR=/var/cache/jarvis-uv /usr/local/bin/uv pip install --python "$OPT/venv/bin/python" --require-hashes --no-deps -r "$req"
  rm -f "$req"; chown -R root:root "$OPT"; chmod -R go-w "$OPT"
fi

say "6 ~/.local/bin/hermes and a restricted ~/.hermes/config.yaml for the agent and the verifier (model: the loopback forwarder, a dummy key)"
for u in agent jarvis-verify; do
  home="$(getent passwd "$u" | cut -d: -f6)"
  if [ "$PLAN" = 1 ]; then echo "   would: $home/.local/bin/hermes -> $OPT/venv/bin/hermes ; $home/.hermes/config.yaml (owner $u, 0600): provider custom, base_url http://127.0.0.1:${FWD_PORT}/v1, tools terminal/file/code/web/delegation only, memory and telemetry off"; continue; fi
  install -d -o "$u" -g "$u" -m 0700 "$home/.local" "$home/.local/bin" "$home/.hermes"
  ln -sfn "$OPT/venv/bin/hermes" "$home/.local/bin/hermes"
  cat > "$home/.hermes/config.yaml" <<CFG
model:
  provider: custom
  base_url: http://127.0.0.1:${FWD_PORT}/v1
  default: jarvis
  api_key: jarvis-local
  context_length: 64000
agent:
  disabled_toolsets:
    - browser
    - computer_use
    - cronjob
    - kanban
    - image_gen
    - video_gen
    - tts
    - vision
    - x_search
    - connections
    - spotify
    - discord
    - discord_admin
    - search
memory:
  memory_enabled: false
  user_profile_enabled: false
telemetry:
  shared_metrics:
    enabled: false
    send: false
tools:
  tool_search:
    enabled: 'off'
CFG
  chown "$u:$u" "$home/.hermes/config.yaml"; chmod 0600 "$home/.hermes/config.yaml"
done

say "7 the model forwarder: root-owned service on 127.0.0.1:${FWD_PORT}; the token file is NOT created here"
if [ "$PLAN" = 1 ]; then
  echo "   would run: install -o root -g root -m 0644 $HERE/model_forwarder.py /opt/jarvis-hub/model_forwarder.py"
  echo "   would run: install -o root -g root -m 0644 $HERE/jarvis-model-forwarder.service /etc/systemd/system/jarvis-model-forwarder.service ; systemctl daemon-reload"
  echo "   would run: systemctl enable --now jarvis-model-forwarder.service  (only when /etc/jarvis-lab/model.token exists, root 600; otherwise installed, not started)"
else
  install -d -o root -g root -m 0755 /opt/jarvis-hub
  install -d -o root -g root -m 0700 /etc/jarvis-lab
  install -o root -g root -m 0644 "$HERE/model_forwarder.py" /opt/jarvis-hub/model_forwarder.py
  install -o root -g root -m 0644 "$HERE/jarvis-model-forwarder.service" /etc/systemd/system/jarvis-model-forwarder.service
  systemctl daemon-reload
  if [ -f /etc/jarvis-lab/model.token ] && [ "$(stat -c '%U:%a' /etc/jarvis-lab/model.token)" = "root:600" ]; then
    systemctl enable --now jarvis-model-forwarder.service
  else
    echo "   model token missing (or not root 600): the forwarder is installed but NOT started; the owner installs /etc/jarvis-lab/model.token, then: systemctl enable --now jarvis-model-forwarder"
  fi
fi

say "8 check (read-only)"
if [ "$PLAN" = 1 ]; then echo "   would run as agent: hermes --version ; and report whether 127.0.0.1:${FWD_PORT} listens"; else
  sudo -u agent -H env -i HOME=/home/agent PATH=/usr/bin:/bin /home/agent/.local/bin/hermes --version 2>&1 | head -3 || true
  ss -ltn | grep -q "127.0.0.1:${FWD_PORT}" && echo "   forwarder listens on 127.0.0.1:${FWD_PORT}" || echo "   forwarder is not running"
fi
say "done. Still by hand: install /etc/jarvis-lab/model.token (kernel token, same lane as the lab agents: public) with scp by path, then enable the forwarder; then the smoke test (agents/test)."
