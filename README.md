# Airi Monitor (vscode-anime-assistent)

> **Airi（愛莉）** — 一个傲娇的二次元桌宠，陪伴你在 VS Code 中写代码。
> 她会监控你的 C/C++/Python 编译错误，用毒舌又暖心的方式吐槽你的 bug。

当前版本：**v0.2.4**

---

## 截图（预期效果）

```
┌──────────────────────┐
│  "笨蛋！括号都配不对  │  ← 聊天气泡（打字机效果，8 秒自动消失）
│   的吗？好好数数！"   │
└──────┬───────────────┘
       │
    ┌──┴──┐
    │ (•̀ᴗ•́)│  ← Q 版角色 / 自定义立绘（可拖拽移动窗口）
    └─────┘
    ● online           ← 在线状态指示
```

窗口透明无边框、置顶显示、可在屏幕任意位置拖动。
角色带情绪表情系统：`idle` / `greeting` / `angry` / `happy` / `surprised`。

---

## 架构

```
┌────────────────────────────────────────────┐
│  VS Code Extension (TypeScript) — 轻量桥接 │
│  src/extension.ts                          │
│                                            │
│  • 监听 C/C++/Python 诊断错误              │
│  • POST /push 转发到桌宠服务器 (19876)     │
│  • checkStandaloneAlive() 健康探测(15s缓存)│
│  • Webview Panel (VS Code 内备用显示)      │
│  • Launch Airi Desktop Pet 一键启动命令    │
└──────────────┬─────────────┬───────────────┘
               │             ▲ POST /push
               ▼             │ (watcher.py 独立监听文件保存)
┌────────────────────────────────────────────┐
│  standalone.py — 独立桌宠服务器             │
│  ThreadingHTTPServer + SSE + pywebview     │
│                                            │
│  • /push 接收诊断 → generate_response      │
│  • SSE 广播 → ui.html 气泡 + 情绪切换      │
│  • assets/character.png 自定义立绘         │
└──────────────┬─────────────────────────────┘
               ▼
┌────────────────────────────────────────────┐
│  python_backend/ — 回复生成（傲娇大脑）     │
│  corpus.py 语料库 / response_generator.py  │
│  本地语料库 或 DeepSeek API 双通道          │
└────────────────────────────────────────────┘
```

- **VS Code 扩展**：只做一件事——把诊断错误转发到桌宠服务器。不启动任何 Python 进程，桌宠不在线时静默跳过。
- **standalone.py**：桌宠本体 + 服务器 + 大脑，完全独立于 VS Code 运行。
- **watcher.py**：可选的独立文件监听器，不用 VS Code 也能用（保存 .py/.c/.cpp 时自动跑语法检查并推送）。

---

## 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| VS Code | ≥ 1.110.0 | 扩展运行环境 |
| Node.js | ≥ 18 | TypeScript 编译（开发扩展时需要） |
| Python | ≥ 3.9 | watcher 与后端均兼容 3.9+（3.12 实测） |
| pywebview | ≥ 4.0 | 桌面宠物窗口 |
| Windows | 10+ | WebView2 运行时（Win11 已内置） |

### 安装步骤

```powershell
# 1. 进入项目目录
cd "S:\My event\projects\vscode-anime-assistent"

# 2. 安装 Node.js 依赖（仅开发/调试扩展时需要）
npm install

# 3. 安装 Python 依赖
pip install -r desktop_pet/requirements.txt
# (可选，启用 DeepSeek AI 回复时)
pip install -r python_backend/requirements.txt

# 4. 编译 TypeScript（仅开发扩展时需要）
npm run compile

# 5. (可选) 启用 DeepSeek AI 回复：设置系统环境变量
# setx DEEPSEEK_API_KEY "sk-xxxxx"
# 可配置项见 .env.example（注意：需设为系统环境变量，代码不自动加载 .env 文件）
```

### 运行（三选一）

```powershell
# 方式一：VS Code 命令面板启动（推荐）
# Ctrl+Shift+P → 输入 "Launch Airi Desktop Pet"
# 扩展会自动查找 Python 并启动桌宠，8 秒内确认启动成功

# 方式二：一键启动脚本（桌宠 + 文件监听器）
start.bat

# 方式三：手动启动
python desktop_pet/standalone.py
```

> 调试扩展本身：在 VS Code 中打开项目 → 按 F5 → 新窗口自动加载扩展。

---

## 项目结构

```
vscode-anime-assistent/
├── src/
│   └── extension.ts              # 扩展入口（轻量诊断桥接器）
│       ├── activate()            # 注册命令 + 诊断监听 + Webview 面板
│       ├── handleDiagnosticsChanged()  # 收集 C/C++/Python 错误
│       ├── checkStandaloneAlive()      # 探测桌宠服务器（15s 缓存）
│       ├── launchStandalonePet()       # 一键启动桌宠命令
│       ├── pushToStandalone()          # POST http://127.0.0.1:19876/push
│       └── getWebviewHtml()            # VS Code 内备用 Webview UI
│
├── desktop_pet/                  # ★ 桌面宠物（独立运行，不依赖 VS Code）
│   ├── standalone.py             # 主入口：HTTP 服务器 + SSE + pywebview 窗口
│   ├── watcher.py                # 独立文件监听器（保存即检查）
│   ├── common.py                 # 共用工具（屏幕尺寸/窗口API/立绘查找/HTML注入）
│   ├── main.py                   # [旧版入口] 需外部 HTTP 服务器，当前链路未使用
│   ├── ui.html                   # 桌宠 UI（SSE 连接、拖拽、气泡、情绪表情）
│   ├── assets/
│   │   └── character.png         # 自定义立绘（可选情绪变体，见下文）
│   ├── launch.bat                # 仅启动桌宠
│   ├── launch_full.bat           # 启动桌宠 + 文件监听器
│   └── requirements.txt          # pywebview>=4.0
│
├── python_backend/               # 回复生成（傲娇大脑）
│   ├── character.py              # Airi 角色人格 + System Prompt
│   ├── corpus.py                 # 场景语料库 + 情绪映射 (EMOTION_MAP)
│   ├── response_generator.py     # 错误分类 + 语料库/DeepSeek API 双通道
│   ├── main.py                   # [旧版] stdin/stdout 后端，当前链路未使用
│   └── requirements.txt          # openai>=1.0.0 (可选，仅 DeepSeek 需要)
│
├── start.bat                     # 一键启动：桌宠 + 文件监听器
├── .env.example                  # 环境变量模板
├── package.json                  # VS Code 扩展清单
└── out/                          # 编译产物 (extension.js)
```

---

## 通信协议

### VS Code 扩展 → 桌宠服务器 (HTTP POST /push)

发现错误时：

```json
{
  "type": "diagnostics",
  "payload": {
    "count": 2,
    "items": [
      {"file": "main.c", "languageId": "c", "message": "expected ';'", "source": "gcc", "line": 12, "character": 5}
    ],
    "timestamp": "2026-09-13T22:00:00.000Z",
    "language": "c"
  }
}
```

错误清零时（仅在服务器在线时发送）：

```json
{"trigger": "all_clear", "error_count": 0, "language": "unknown", "files": [], "sample_errors": []}
```

### 桌宠服务器 → ui.html (SSE /events)

```
data: {"type":"chatMessage","payload":{"text":"哼，终于修好了。","emotion":"happy"}}

data: {"type":"errorAlert","payload":{"text":"笨蛋！括号都配不对！","emotion":"angry"}}
```

### HTTP 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/push` | POST | 接收诊断 / all_clear 事件（扩展和 watcher.py 都推这里） |
| `/events` | GET | SSE 事件流（桌宠窗口连接） |
| `/ping` | GET | 健康检查 → `{"status":"ok","server":"airi-standalone"}` |
| `/event` | POST | 接收桌宠窗口消息（如 `desktopReady`） |

---

## 功能特性

### 桌面宠物窗口
- 🖱️ **拖拽移动**：按住角色拖到屏幕任意位置
- 📌 **始终置顶**：透明无边框，悬浮在所有窗口之上
- 🫧 **聊天气泡**：打字机效果 + 错误红色边框 + 8 秒自动消失（最多 3 条）
- 😤 **情绪表情系统**：每条回复带情绪，角色表情自动切换，8 秒后回落 idle
- 🖱️ **右键菜单**：退出 Airi（销毁窗口并结束进程，重开用启动命令/脚本）
- 🔄 **自动重连**：SSE 断线后自动恢复连接

### 自定义立绘

把图片放到 `desktop_pet/assets/character.png` 即可替换默认的 CSS Q 版角色。
支持情绪变体：在同目录放置 `character_angry.png`、`character_happy.png`、
`character_surprised.png`、`character_greeting.png`，缺失的情绪会自动回落到默认图。

### VS Code 集成
- 诊断错误实时转发（C/C++/Python）
- Webview 面板作为备用显示（不依赖 Python 环境）
- 健康探测：桌宠不在线时不发送任何请求

### 回复策略
1. **DeepSeek API**（设置 `DEEPSEEK_API_KEY` 环境变量）→ AI 生成个性化傲娇回复
2. **本地语料库**（默认）→ 按错误类型（语法/类型/导入/未定义名等）从对应场景随机抽取台词

---

## 命令

在 VS Code 中按 `Ctrl+Shift+P`：

| 命令 | 说明 |
|------|------|
| `Launch Airi Desktop Pet` | 一键启动桌宠（自动查找 Python，启动后确认服务器在线） |
| `Open Anime Assistant` | 打开 VS Code 内置助手面板（备用显示，按需打开） |

---

## 常见问题

### Q: 桌面宠物没有出现？

1. 确认已安装 pywebview：`pip show pywebview`（注意用你启动时的那个 Python）
2. 确认 Python ≥ 3.9：`python --version`
3. 用命令面板 `Launch Airi Desktop Pet` 启动，失败会有具体错误提示
4. 手动运行 `python desktop_pet/standalone.py` 看控制台报错

### Q: 右键退出了 Airi，怎么再打开？

重新执行 `Launch Airi Desktop Pet` 命令，或运行 `start.bat` /
`python desktop_pet/standalone.py`。（v0.2.4 起右键菜单是「退出」而非「隐藏」，
旧版本隐藏后无法找回，只能结束 python 进程。）

### Q: 拖拽时窗口跳到屏幕左上角？

已通过 Python API `get_position()` 修复。如仍出现，确认 pywebview ≥ 4.0。

### Q: 窗口不是透明的？

- Windows 10：需安装 Edge WebView2 运行时
- Windows 11：已内置，无需额外安装
- 非 Windows：透明窗口支持取决于系统 WebView

### Q: 气泡不显示？

桌宠窗口会自动重连 SSE。确认 19876 端口没被占用：`netstat -ano | findstr 19876`。

### Q: 如何换掉默认角色？

把你的立绘放到 `desktop_pet/assets/character.png`（支持可选的情绪变体，见上文）。

---

## 开发

```powershell
# 编译
npm run compile

# 监听模式
npm run watch

# 代码检查
npm run lint

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
| 0.2.0 | 2026-07-19 | **独立桌宠架构**：standalone.py 内置服务器 + SSE + pywebview，watcher.py 独立文件监听，情绪立绘系统 |
| 0.2.1 | 2026-09-13 | 重建丢失的 common.py（修复启动崩溃）、`Launch Airi Desktop Pet` 一键启动命令、健康探测、Webview 转义修复 |
| 0.2.2 | 2026-09-14 | all_clear 误报修复（双侧）、watcher 错误解析/语言ID修复、SSE 序号防错位、推送去重、补 publisher 字段 |
| 0.2.3 | 2026-09-16 | 错误分类误判修复（NameError 被错判为类型错误）、单文件 watcher 去重、情绪回落立绘复位 |
| 0.2.4 | 2026-09-19 | 诊断统计覆盖未打开文档、端口冲突防僵尸窗口、右键「隐藏」改「退出」、watcher 不写 __pycache__/只推 error/变更限流、Py3.9 兼容、环境变量补实现、移除 C_Cpp_Runner 配置与 helloWorld |

详见 [CHANGELOG.md](CHANGELOG.md)。
