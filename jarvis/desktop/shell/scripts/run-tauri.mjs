import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ensureLoopbackNoProxy } from "./proxy-env.mjs";

const env = { ...process.env };

// The Tauri window first loads Vite and then navigates to the local Python
// backend. Keep loopback traffic out of user-configured proxies; otherwise a
// transparent window can silently display an empty proxy error response.
ensureLoopbackNoProxy(env);

// niri has a compositor-native GTK input-region implementation. Other Linux
// desktops keep the proven X11/XShape path until they receive the same backend.
if (process.platform === "linux") {
  env.GDK_BACKEND = env.NIRI_SOCKET ? "wayland" : "x11";

  const layerShell = spawnSync(
    "pkg-config",
    ["--exists", "gtk-layer-shell-0"],
    { env, stdio: "ignore" },
  );
  if (layerShell.status !== 0) {
    console.error(
      [
        "缺少 Linux 原生开发依赖 gtk-layer-shell。",
        "Debian/Ubuntu 可执行：sudo apt install libgtk-layer-shell-dev",
        "需要由该软件包提供 gtk-layer-shell-0.pc 后再启动 Tauri。",
      ].join("\n"),
    );
    process.exit(1);
  }
}

if (process.platform === "linux" && env.SNAP) {
  const realHome = env.SNAP_REAL_HOME || env.HOME;

  if (env.XDG_DATA_DIRS_VSCODE_SNAP_ORIG) {
    env.XDG_DATA_DIRS = env.XDG_DATA_DIRS_VSCODE_SNAP_ORIG;
  }
  if (env.XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG) {
    env.XDG_CONFIG_DIRS = env.XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG;
  }
  if (realHome && env.XDG_DATA_HOME?.includes("/snap/")) {
    env.XDG_DATA_HOME = `${realHome}/.local/share`;
  }

  const snapRuntimeKeys = [
    "GDK_PIXBUF_MODULEDIR",
    "GDK_PIXBUF_MODULE_FILE",
    "GIO_LAUNCHED_DESKTOP_FILE",
    "GIO_LAUNCHED_DESKTOP_FILE_PID",
    "GIO_MODULE_DIR",
    "GSETTINGS_SCHEMA_DIR",
    "GTK_EXE_PREFIX",
    "GTK_IM_MODULE_FILE",
    "GTK_MODULES",
    "GTK_PATH",
    "LOCPATH",
    "SNAP",
    "SNAP_ARCH",
    "SNAP_COMMON",
    "SNAP_CONTEXT",
    "SNAP_COOKIE",
    "SNAP_DATA",
    "SNAP_EUID",
    "SNAP_INSTANCE_NAME",
    "SNAP_LAUNCHER_ARCH_TRIPLET",
    "SNAP_LIBRARY_PATH",
    "SNAP_NAME",
    "SNAP_REAL_HOME",
    "SNAP_REVISION",
    "SNAP_UID",
    "SNAP_USER_COMMON",
    "SNAP_USER_DATA",
    "SNAP_VERSION",
  ];

  for (const key of snapRuntimeKeys) {
    delete env[key];
  }
}

const tauriCli = fileURLToPath(
  new URL("../node_modules/@tauri-apps/cli/tauri.js", import.meta.url),
);
const result = spawnSync(
  process.execPath,
  [tauriCli, ...process.argv.slice(2)],
  { env, stdio: "inherit" },
);

if (result.error) {
  throw result.error;
}
if (result.signal) {
  process.kill(process.pid, result.signal);
}

process.exit(result.status ?? 1);
