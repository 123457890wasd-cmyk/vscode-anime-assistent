# Airi Monitor (vscode-anime-assistent)
请注意，当前这个项目只是最初版本，可能会有很大的变化。
Please note that this project is currently in its initial stage and is subject to significant changes.
> **Airi（愛莉）** — 一个傲娇的二次元桌宠，陪伴你在 VS Code 中写代码。  
> 她会监控你的 C/C++/Python 编译错误，用毒舌又暖心的方式吐槽你的 bug。

---

## 截图（预期效果）

```
┌──────────────────────┐
│  "笨蛋！括号都配不对  │  ← 聊天气泡（自动弹出/消失）
│   的吗？好好数数！"   │
└──────┬───────────────┘
       │
    ┌──┴──┐
    │  A  │  ← 圆形头像（可拖拽移动窗口）
    └─────┘
    Airi
    ● online           ← 在线状态指示
```

窗口透明无边框、置顶显示、可在屏幕任意位置拖动。

---

## 架构

```
┌─────────────────────────────────────────┐
│  VS Code Extension (TypeScript)         │
│  src/extension.ts                       │
│                                         │
│  • 监听 C/C++/Python 诊断错误           │
│  • 启动 Python 后端 (stdin/stdout)      │
│  • 启动 HTTP SSE 服务器 (localhost)     │
│  • 启动桌面宠物进程                     │
│  • VS Code Webview Panel (兼容模式)     │
└──────┬──────────────┬───────────────────┘
       │              │
       ▼              ▼ SSE (HTTP)
┌──────────────┐  ┌──────────────────────┐
│ Python 后端   │  │ Desktop Pet 窗口     │
│              │  │ (pywebview)          │
│ main.py      │  │                      │
│ character.py │  │ • 透明无边框窗口      │
│ corpus.py    │  │ • EventSource 接收    │
│ response_    │  │ • 可拖拽移动          │
│ generator.py │  │ • 聊天气泡动画        │
└──────────────┘  └──────────────────────┘
```

- **Python 后端**：分析错误类型 → 从语料库挑选傲娇台词（或调用 DeepSeek API）
- **SSE 推送**：Python 的回复通过 Extension 广播到桌面宠物窗口
- **桌面宠物**：独立于 VS Code 的透明窗口，可拖到桌面任意位置

---

## 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| VS Code | ≥ 1.110.0 | 扩展开发环境 |
| Node.js | ≥ 18 | TypeScript 编译 |
| Python | ≥ 3.9 | 后端 + 桌面窗口 |
| pywebview | ≥ 4.0 | 桌面宠物窗口 |
| Windows | 10+ | WebView2 运行时（Win11 已内置） |

### 安装步骤

```powershell
# 1. 进入项目目录
cd "S:\My event\projects\vscode-anime-assistent"

# 2. 安装 Node.js 依赖
npm install

# 3. 安装 Python 依赖
pip install -r python_backend/requirements.txt
pip install -r desktop_pet/requirements.txt

# 4. 编译 TypeScript
npm run compile

# 5. (可选) 配置 DeepSeek API Key 启用 AI 回复
# set DEEPSEEK_API_KEY=sk-xxxxx
```

### 运行

```powershell
# 方式一：VS Code 扩展开发模式（推荐）
# 在 VS Code 中打开项目 → 按 F5 → 新窗口自动加载扩展

# 方式二：命令行手动启动桌面宠物（调试用）
python desktop_pet/main.py --port 9876
# 需要先启动 VS Code 扩展让 HTTP 服务器运行
```

---

## 项目结构

```
vscode-anime-assistent/
├── src/
│   └── extension.ts              # 扩展入口 (TypeScript)
│       ├── activate()            # 启动 Python 后端 + HTTP SSE 服务器 + 桌面宠物
│       ├── startDesktopServer()  # HTTP SSE 服务器（localhost 随机端口）
│       ├── spawnDesktopPet()     # 启动 desktop_pet/main.py
│       ├── broadcastToDesktop()  # 向 SSE 客户端推送消息
│       ├── handlePythonLine()    # 解析 Python 回复 → Webview + Desktop
│       └── getWebviewHtml()      # VS Code 内置 Webview UI（兼容模式）
│
├── desktop_pet/                  # ★ 桌面宠物窗口（新增）
│   ├── main.py                   # pywebview 入口 + WindowAPI
│   ├── ui.html                   # 桌宠 UI（SSE 连接、拖拽、气泡）
│   └── requirements.txt          # pywebview>=4.0
│
├── python_backend/               # Python 后端（回复生成）
│   ├── main.py                   # stdin/stdout 消息循环
│   ├── character.py              # Airi 角色人格 + System Prompt
│   ├── corpus.py                 # 9 类场景、40+ 条傲娇台词
│   ├── response_generator.py     # 错误分类 + 语料库/API 双通道
│   └── requirements.txt          # openai>=1.0.0 (可选)
│
├── package.json                  # VS Code 扩展清单
├── tsconfig.json                 # TypeScript 配置
└── out/                          # 编译产物
    └── extension.js
```

---

## 通信协议

### Extension → Desktop Pet (SSE)

```
data: {"type":"chatMessage","payload":{"text":"哼，终于修好了。"}}

data: {"type":"errorAlert","payload":{"text":"笨蛋！括号都配不对！"}}

data: {"type":"statusChange","payload":{"text":"Airi 已就绪"}}

data: {"type":"backendExit","payload":{"code":0,"signal":null}}
```

### Desktop Pet → Extension (HTTP POST)

```json
POST /event
{"type":"desktopReady"}
```

### HTTP 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/events` | GET | SSE 事件流（桌面宠物连接） |
| `/ping` | GET | 健康检查 → `{"status":"ok","port":xxxx}` |
| `/event` | POST | 接收桌面宠物消息 |

---

## 功能特性

### 桌面宠物窗口
- 🖱️ **拖拽移动**：按住头像拖动到屏幕任意位置
- 📌 **始终置顶**：悬浮在所有窗口之上
- 🫧 **聊天气泡**：弹入动画 + 粉色错误边框 + 8 秒自动消失
- 🖱️ **右键菜单**：隐藏 Airi（可通过 VS Code 命令恢复）
- 🔄 **自动重连**：VS Code 重启后自动恢复连接
- 🎨 **透明背景**：融入桌面，类似真正的桌宠

### VS Code 集成（兼容模式）
- 诊断错误监听 → Python 分析 → 傲娇吐槽
- Webview Panel 作为备选显示
- 命令面板：`Toggle Desktop Pet` 开关桌宠窗口

### 回复策略
1. **DeepSeek API**（设置 `DEEPSEEK_API_KEY` 环境变量）→ AI 生成个性化傲娇回复
2. **本地语料库**（默认）→ 从 9 类场景随机抽取预写台词

---

## 命令

在 VS Code 中按 `Ctrl+Shift+P`：

| 命令 | 说明 |
|------|------|
| `Open Anime Assistant` | 打开 VS Code 内置助手面板 |
| `Toggle Desktop Pet` | 开关桌面宠物窗口 |

---

## 常见问题

### Q: 桌面宠物没有出现？

1. 确认已安装 pywebview：`pip show pywebview`
2. 确认 Python 在 PATH 中：`python --version`
3. 查看 VS Code 开发者控制台（`Ctrl+Shift+I`）→ 搜索 `[airi-monitor]` 日志
4. 手动测试：`python desktop_pet/main.py --port 9876`（端口号见控制台日志）

### Q: 拖拽时窗口跳到屏幕左上角？

已通过 Python API `get_position()` 修复。如仍出现，确认 pywebview ≥ 4.0。

### Q: 窗口不是透明的？

- Windows 10：需安装 Edge WebView2 运行时
- Windows 11：已内置，无需额外安装
- 非 Windows：透明窗口支持取决于系统 WebView

### Q: 气泡不显示？

检查 VS Code 开发者控制台是否有 SSE 连接错误。桌宠窗口会自动重连。

### Q: 如何换掉 "A" 头像？

编辑 `desktop_pet/ui.html`，将 `.avatar` 内的 `A` 替换为 `<img src="your_image.png">`。

---

## 开发

```powershell
# 编译
npm run compile

# 监听模式
npm run watch

# 运行测试
npm test

# 打包 .vsix
npm install -g @vscode/vsce
vsce package
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-06-23 | 傲娇人格、聊天气泡 UI、Python 后端、DeepSeek API 预留 |
| 0.2.0 | 2026-06-24 | **方案 B 混合架构**：桌面宠物窗口（pywebview + SSE）、可拖拽透明窗口、VS Code 插件 + 独立桌宠双模 |
