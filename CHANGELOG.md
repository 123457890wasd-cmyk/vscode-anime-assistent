# Change Log

## [0.2.0] — 2026-06-24

### Added
- **桌面宠物窗口**（方案 B 混合架构）：`desktop_pet/` 目录
  - Python + pywebview 透明无边框置顶窗口
  - 可拖拽移动（按住头像拖动到屏幕任意位置）
  - 聊天气泡弹入动画 + 8 秒自动消失
  - 右键菜单（隐藏 Airi）
- HTTP SSE 服务器（Node.js 内置 `http` 模块，零额外依赖）
  - 自动分配空闲端口（`listen(0)`）
  - SSE keepalive 心跳（每 20 秒）
  - 健康检查端点 `/ping`
- SSE 客户端广播机制：Python 回复同时推送到 Webview 和桌面宠物
- `Toggle Desktop Pet` VS Code 命令
- 优雅降级：Python/pywebview 不可用时仅使用 VS Code Webview
- 内联 Fallback HTML（`main.py` 内），ui.html 缺失时不崩溃
- 窗口位置通过 Python API `get_position()` 获取，避免 `window.screenX` 不准确

### Changed
- `extension.ts`：新增 `startDesktopServer()`, `broadcastToDesktop()`, `spawnDesktopPet()`, `killDesktopPet()`
- 进程生命周期：扩展停用时自动终止桌面宠物 + 关闭 HTTP 服务器
- `package.json`：新增 `toggleDesktopPet` 命令

### Fixed
- 拖拽起始点使用 Python API 而非 `window.screenX/Y`
- tkinter 屏幕尺寸获取用 `try/finally` 保证 `root.destroy()`
- Windows 屏幕尺寸优先用 `ctypes` 避免 tkinter 窗口闪烁
- 桌面宠物初始状态显示 "connecting..." 而非 "online"

## [0.1.0] — 2026-06-23

### Added
- 聊天式 Webview UI（对话气泡 + 傲娇粉色主题）
- Airi 角色人格：傲娇（ツンデレ），名字「愛莉」
- Python 后端：本地语料库随机抽取回复
- DeepSeek API 接口预留（设置 `DEEPSEEK_API_KEY` 启用）
- 错误清零检测（all_clear 事件）
- 启动问候 + 打字指示器动画

### Changed
- Webview 从诊断卡片列表改为聊天对话界面
- 诊断事件统一由 Python 后端处理后下发 Webview
- 消息协议新增 `chatMessage`, `errorAlert`, `statusChange` 类型

## [0.0.1] — Initial scaffold
- VS Code 扩展模板
- 基础诊断监听 + Python stub
