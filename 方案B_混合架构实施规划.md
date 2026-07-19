# Airi Monitor — 方案 B 混合架构实施规划

> 日期：2026-06-24  
> 目标：将 Airi 从 VS Code 内部 Webview Panel 改造为 **可拖动的桌面桌宠**，同时保留 VS Code 插件身份。

---

## 1. 架构决策

**方案 B：VS Code 插件 + 独立桌面进程（混合架构）**

| 选型 | 决定 | 理由 |
|------|------|------|
| 桌面窗口技术 | Python + `pywebview` | 轻量（~5MB）、用系统原生 WebView、复用现有 HTML/CSS/JS |
| 通信方式 | HTTP SSE（Server-Sent Events） | 单向推送为主，适合消息通知场景；Node.js 内置 `http` 模块零依赖 |
| 窗口类型 | 透明无边框、置顶、小尺寸 | 桌宠标准形态 |
| 进程管理 | Extension spawn → Extension kill | 生命周期与 VS Code 绑定 |

---

## 2. 数据流

```
┌─────────────────────────────────────────────────┐
│  VS Code Extension (TypeScript)                  │
│                                                  │
│  onDidChangeDiagnostics ──→ Python backend       │
│  onDidChangeTextDocument ──→ Python backend      │
│                                                  │
│  Python stdout ──→ handlePythonLine()            │
│                      ├──→ postToWebview()        │
│                      └──→ broadcastToDesktop() ──┐
│                                                  │
│  HTTP Server (127.0.0.1:<random>)               │
│  GET /events ── SSE stream                       │
│  GET /ping   ── health check                     │
│  POST /event ── receive from desktop             │
└──────────────────────────┬──────────────────────┘
                           │ SSE (localhost)
┌──────────────────────────▼──────────────────────┐
│  Desktop Pet (Python + pywebview)                │
│                                                  │
│  透明无边框窗口（~280x420px）                     │
│  EventSource → 接收 chatMessage / errorAlert     │
│  拖拽移动、置顶显示、气泡动画                      │
│  复用现有 CSS/JS（去 vscode API）                 │
└─────────────────────────────────────────────────┘
```

---

## 3. 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `desktop_pet/main.py` | 桌面宠物 Python 入口，pywebview 窗口管理 |
| `desktop_pet/ui.html` | 桌面宠物 UI（从 extension.ts 提取 + 改造） |
| `desktop_pet/requirements.txt` | Python 依赖：`pywebview` |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/extension.ts` | 添加 HTTP SSE 服务器 + 广播函数 + 桌面进程 spawn/kill |
| `package.json` | 添加 `toggleDesktopPet` 命令 + 编译时复制 desktop_pet 目录 |

---

## 4. 通信协议

### SSE 事件格式
```
data: {"type":"chatMessage","payload":{"text":"哼，终于修好了。"}}

data: {"type":"errorAlert","payload":{"text":"笨蛋！括号都配不对！"}}

data: {"type":"statusChange","payload":{"text":"Airi 已就绪"}}

data: {"type":"backendExit","payload":{"code":0,"signal":null}}
```

### 桌面 → 扩展（POST /event）

```json
{"type":"desktopReady"}
```
用于桌面窗口通知扩展它已准备好接收消息。

---

## 5. 实施步骤

### Step 1: 修改 extension.ts — 添加 HTTP SSE 服务器
- 使用 Node.js 内置 `http` 模块（零额外依赖）
- 自动分配空闲端口
- SSE `/events` 端点：保持连接，推送 JSON 事件
- `/ping` 健康检查
- `POST /event` 接收桌面端消息
- CORS 头允许本地访问

### Step 2: 修改 extension.ts — 广播 + 进程管理
- `broadcastToDesktopClients()`: 将 Python 响应同时推送到 Webview 和 SSE 客户端
- `spawnDesktopPet(port)`: 启动 `python desktop_pet/main.py --port XXXX`
- `killDesktopPet()`: 扩展停用时终止桌面进程
- 在 `deactivate()` 中清理

### Step 3: 创建 desktop_pet/main.py
- 从命令行参数获取端口号
- 读取 `ui.html`，注入端口占位符
- 创建 pywebview 窗口（frameless, transparent, on_top）
- 暴露 Python API 给 JS（窗口移动）

### Step 4: 创建 desktop_pet/ui.html
- 从现有 `getWebviewHtml()` 提取 HTML/CSS/JS
- 移除 `acquireVsCodeApi` / `vscode.postMessage`
- 添加 `EventSource` 连接到 SSE
- 添加拖拽逻辑（mousedown/mousemove/mouseup）
- 适配桌面窗口尺寸

### Step 5: 验证
- 编译 TypeScript 无错误
- 安装 Python 依赖
- 端到端测试

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| pywebview 透明窗口在部分 Windows 版本不支持 | 降级为非透明 + 圆角边框 |
| 用户未安装 Python 或 pywebview | 优雅降级：仅使用 VS Code Webview，不弹桌面窗口 |
| 端口冲突 | 使用 `listen(0)` 自动分配空闲端口 |
| SSE 客户端断开未清理 | `req.on('close')` 时从 clients 数组中移除 |
| 多个 VS Code 窗口同时运行 | 每个窗口独立端口，不会冲突 |
| 桌面窗口被杀后 VS Code 不知情 | 桌面窗口定时发送心跳（EventSource 自带重连） |

---

## 7. 开发环境要求

- Node.js 18+（VS Code 扩展开发）
- Python 3.9+
- `pip install pywebview`（桌面窗口）
- Windows 10+（WebView2 运行时，Win11 已内置）
