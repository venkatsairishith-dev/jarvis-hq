"""The supervisor (plan P1 step 4): starts the manifest's components in dependency order inside one ProcessTree, waits for each to be
healthy, and stops everything on exit. If this process dies, the Job Object (Windows) takes every child with it.

    python jarvis/supervisor/core.py MANIFEST.json [--run-dir DIR]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import boot_token  # noqa: E402
import health  # noqa: E402
import jobobject  # noqa: E402
import manifest  # noqa: E402


class Supervisor:
    def __init__(self, comps: list[manifest.Component], run_dir: Path) -> None:
        self.comps = manifest.order(comps)
        self.run_dir = Path(run_dir)
        self.tree = jobobject.ProcessTree()
        self.procs: dict[str, object] = {}
        self.token: str | None = None

    def start(self) -> None:
        self.token = boot_token.rotate(self.run_dir)
        for c in self.comps:
            env = {**c.env, "JARVIS_BOOT_TOKEN_FILE": str(self.run_dir / boot_token.TOKEN_FILE)}   # the file path, never the token
            self.procs[c.name] = self.tree.start(c.argv, cwd=c.cwd, env=env)
            if c.health_kind and not health.wait_healthy(c.health_kind, c.host, c.port, c.health_path, c.health_timeout_s):
                self.stop()
                raise RuntimeError(f"{c.name} did not become healthy within {c.health_timeout_s}s")

    def status(self) -> dict[str, str]:
        out = {}
        for c in self.comps:
            p = self.procs.get(c.name)
            out[c.name] = "not started" if p is None else ("running" if p.poll() is None else f"exited {p.returncode}")
        return out

    def stop(self) -> None:
        self.tree.close()

    def watch(self, poll_s: float = 1.0) -> int:
        """Block until a component with restart=never exits (then stop all, return 1) or until interrupted."""
        by = {c.name: c for c in self.comps}
        try:
            while True:
                for name, p in list(self.procs.items()):
                    if p.poll() is None:
                        continue
                    c = by[name]
                    if c.restart == "on-failure" and p.returncode != 0:
                        self.procs[name] = self.tree.start(c.argv, cwd=c.cwd, env=c.env)
                    else:
                        print(f"{name} exited with {p.returncode}; stopping everything")
                        return 1
                time.sleep(poll_s)
        except KeyboardInterrupt:
            return 0
        finally:
            self.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--run-dir", type=Path, default=Path(r"D:\jarvis\tmp\run"))
    args = ap.parse_args(argv)
    sup = Supervisor(manifest.load(args.manifest), args.run_dir)
    sup.start()
    print("started:", sup.status())
    return sup.watch()


if __name__ == "__main__":
    sys.exit(main())
