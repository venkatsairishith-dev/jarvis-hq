import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const backendSnapshotPaths = [
  "brain",
  "config",
  "frontend",
  "main_logic",
  "main_routers",
  "memory",
  "static",
  "templates",
  "utils",
  "agent_server.py",
  "launcher.py",
  "main_server.py",
  "memory_server.py",
  "pyproject.toml",
  "requirements.txt",
  "uv.lock",
];

describe("repository boundary", () => {
  it("does not vendor the N.E.K.O backend snapshot", () => {
    for (const path of backendSnapshotPaths) {
      expect(existsSync(resolve(path)), path).toBe(false);
    }
  });

  it("injects only a release artifact instead of Python source", () => {
    const defaultConfig = JSON.parse(
      readFileSync(resolve("src-tauri", "tauri.conf.json"), "utf8"),
    );
    const releaseConfig = JSON.parse(
      readFileSync(resolve("src-tauri", "tauri.release.conf.json"), "utf8"),
    );

    expect(defaultConfig.bundle.resources).toBeUndefined();
    expect(releaseConfig.bundle.resources).toEqual({
      "../.artifacts/backend/": "backend/",
    });
  });

  it("does not build a copied upstream chat frontend", () => {
    const packageJson = JSON.parse(readFileSync(resolve("package.json"), "utf8"));
    expect(JSON.stringify(packageJson.scripts)).not.toContain("../frontend");
  });
});
