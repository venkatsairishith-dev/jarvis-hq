# N.E.K.O. Tauri

N.E.K.O. 的实验性 Tauri 桌面外壳。

这个仓库只维护桌面宿主能力：窗口、鼠标穿透、平台适配、原生屏幕捕获，以及后端进程的启动与退出。N.E.K.O 的 Python 服务、业务逻辑、模型配置和共享 Web UI 均由上游仓库维护，不在这里复制。

- 上游 N.E.K.O：[Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O)
- Electron 桌面壳参考：[Project-N-E-K-O/N.E.K.O.-PC](https://github.com/Project-N-E-K-O/N.E.K.O.-PC)
- Tauri 宿主兼容 PR：[N.E.K.O #2488](https://github.com/Project-N-E-K-O/N.E.K.O/pull/2488)

## 仓库边界

| 内容 | 维护位置 |
| --- | --- |
| Python 后端、模型与记忆逻辑 | `N.E.K.O` |
| 聊天、Live2D/VRM/MMD 等共享 Web UI | `N.E.K.O` |
| Tauri 启动页与原生桥接 | 本仓库 |
| Windows / macOS / Linux 窗口与命中区适配 | 本仓库 |
| 发布用后端可执行文件 | 构建时从 N.E.K.O 发布产物注入，不提交到 Git |

运行时流程：

```text
Tauri 启动页
    │
    ├─ 发现 127.0.0.1:48911 已有 N.E.K.O → 直接连接，不接管该进程
    │
    └─ 没有已运行服务
         ├─ 开发：通过 NEKO_BACKEND_DIR 启动外部 N.E.K.O 源码
         └─ 发布：启动构建时注入的 projectneko_server
```

## 环境要求

- Node.js 20.19 或更高版本
- Rust stable
- Tauri 2 对应的平台系统依赖
- 开发后端时需要单独 checkout `N.E.K.O`，并在其目录执行 `uv sync --frozen`

Linux 还需要 WebKitGTK、GTK 3、gtk-layer-shell、X11 等开发包。Debian / Ubuntu 可从下面的基础依赖开始：

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev \
  libgtk-layer-shell-dev libx11-dev librsvg2-dev patchelf
```

## 本地开发

安装桌面壳依赖：

```bash
npm ci
```

推荐先单独启动 N.E.K.O 后端，再启动 Tauri：

```bash
# 在 N.E.K.O 仓库
uv sync --frozen
uv run launcher.py

# 在本仓库
npm run tauri:dev
```

如果希望 Tauri 代为启动开发后端，可传入后端仓库绝对路径：

```bash
NEKO_BACKEND_DIR=/absolute/path/to/N.E.K.O npm run tauri:dev
```

也可以直接指定一个已经构建好的后端程序：

```bash
NEKO_BACKEND_EXECUTABLE=/absolute/path/to/projectneko_server npm run tauri:dev
```

当 `48911` 端口已经存在 N.E.K.O 服务时，桌面壳只会连接，不会在退出时关闭外部启动的后端。

## 测试

```bash
npm run check
cargo test --manifest-path src-tauri/Cargo.toml --locked
```

`npm run check` 会依次执行 TypeScript 类型检查、Tauri 注入桥的 Vitest 测试和 Vite 生产构建。

## 发布构建

本仓库不保存 `.venv` 或 Python 源码快照。发布流水线需要先从 N.E.K.O 生成当前平台的单文件后端产物，再放到：

```text
.artifacts/backend/projectneko_server       # macOS / Linux
.artifacts/backend/projectneko_server.exe   # Windows
```

然后执行：

```bash
npm run tauri:build
```

构建脚本会先检查产物是否存在（Unix 平台还会检查执行权限），再通过 `src-tauri/tauri.release.conf.json` 将它作为只读资源打入安装包。

## 目录结构

```text
.
├── src/                  # 最小启动页与注入桥测试
├── src-tauri/
│   ├── src/platform/     # 各平台窗口、命中区实现
│   ├── src/screen_capture.rs
│   ├── src/backend_runtime.rs
│   └── tauri.conf.json
├── scripts/              # 启动环境与发布产物检查
├── index.html
└── package.json
```

## 贡献边界

- 通用 Web 功能或后端逻辑：提交到 `N.E.K.O`。
- Electron 专属行为：提交到 `N.E.K.O.-PC`。
- Tauri/Rust、WebView 或平台窗口问题：提交到本仓库。

如果一个修复同时涉及上游与 Tauri，请拆成两个 PR：先定义上游共享契约，再在本仓库实现 Tauri provider。这样可以避免桌面壳长期携带一份过期的后端副本。
