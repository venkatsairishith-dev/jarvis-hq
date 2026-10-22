import { constants, accessSync } from "node:fs";
import { resolve } from "node:path";

const executableName = process.platform === "win32"
  ? "projectneko_server.exe"
  : "projectneko_server";
const artifactPath = resolve(".artifacts", "backend", executableName);

try {
  accessSync(
    artifactPath,
    process.platform === "win32" ? constants.F_OK : constants.F_OK | constants.X_OK,
  );
} catch {
  console.error([
    `缺少当前平台的 N.E.K.O 后端构建产物：${artifactPath}`,
    "请先从 N.E.K.O 的发布流程取得 projectneko_server，",
    "放入 .artifacts/backend/ 后再执行 npm run tauri:build。",
  ].join("\n"));
  process.exit(1);
}

console.log(`[release] 后端构建产物已就绪：${artifactPath}`);
