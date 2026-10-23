#!/bin/sh
# Copy the pinned requirements from ~/hq (jarvis/tools/requirements/agent-venv.txt) and rebuild the jarvis-test image.
set -e
cd "$(dirname "$0")"
cp ~/hq/jarvis/tools/requirements/agent-venv.txt .
podman build -t jarvis-test .
podman run --rm jarvis-test python -c "import importlib.metadata as m; print('mcp', m.version('mcp'), 'pydantic', m.version('pydantic'))"
