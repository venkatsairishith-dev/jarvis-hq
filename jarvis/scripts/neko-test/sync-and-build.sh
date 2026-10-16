#!/bin/sh
# Copy the dependency files from ~/hq-neko (pyproject.toml, uv.lock, deps/) and rebuild the neko-test image.
set -e
cd "$(dirname "$0")"
cp ~/hq-neko/pyproject.toml ~/hq-neko/uv.lock .
rm -rf deps && cp -r ~/hq-neko/deps deps
podman build -t neko-test .
podman run --rm neko-test python -c "import importlib.metadata as m; print('starlette', m.version('starlette'), 'pydantic', m.version('pydantic'))"
