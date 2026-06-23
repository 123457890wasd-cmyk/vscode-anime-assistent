# Change Log

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
