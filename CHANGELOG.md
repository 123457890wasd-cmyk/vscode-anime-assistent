# Change Log

## [0.2.4] — 2026-09-19

### Fixed
- **关掉报错文件后误报 all_clear**：`vscode.languages.getDiagnostics()` 会保留已关闭文档的诊断，
  而旧逻辑只统计打开的文档，关掉报错文件就触发"全部清零"。现在按工作区整体诊断统计；
  已关闭文档的语言按扩展名推断（.py/.c/.cpp/.h 等）
- **端口冲突防僵尸窗口**：`AIRI_STANDALONE_PORT` 被占用时启动 standalone.py，
  现在通过 `_ping_ok()` 区分"已有 Airi 实例"（静默退出）与"无关进程占用端口"（报错退出），
  不再弹出第二个连不上服务器的桌宠窗口
- **右键「隐藏 Airi」改为「退出 Airi」**：隐藏后窗口无恢复入口且进程仍占端口，属 UX 陷阱；
  现在 `exit_app()` 销毁窗口并结束进程，重开用启动命令或脚本
- **watcher 不再写 `__pycache__`**：Python 检查从 `py_compile` 子进程改为进程内 `compile()`
  （`tokenize.open` 处理编码声明），检查目录不再生成缓存文件
- **watcher 只对 gcc error 响应**：原来 warning 也计入错误数，编译警告会打断"全部清零"的判断。
  现在只收集 `error:` 行
- **watcher 变更限流**：单轮扫描最多检查 20 个变更文件（MAX_CHECKS_PER_SCAN），
  git checkout 等大批量变更不再卡住轮询；未检查的文件顺延到下一轮重查（mtime 延迟提交）
- **watcher 签名含错误数**：内容签名加入 error_count，错误数量变化时能正确触发推送
- **ui.html 同情绪消息不再吞掉回落计时**：相同情绪连续推送时计时器现在会重置，
  气泡/表情的 8 秒回落与新消息对齐；无边框窗口上屏蔽 Chromium 默认右键菜单
- **standalone.py 恶意/异常 Content-Length 防护**：/push 与 /event 的请求头解析包 try/except，
  非数字不再抛异常中断服务器线程
- **python_backend Py3.9 兼容**：`str | None` 注解改为 `Optional[str]`，3.9 导入不再崩溃
- **launch_full.bat 编码修复**：GBK+LF 转为 UTF-8+CRLF，与文件头 `chcp 65001` 匹配

### Changed
- **环境变量补实现**：`.env.example` 声明的 `AIRI_STANDALONE_PORT`（扩展 + standalone.py 双侧）
  与 `AIRI_CHARACTER_IMAGE`（立绘路径覆盖）现在真正被代码读取
- **Webview 面板改为按需打开**：启动时不再自动弹出，用命令 `Open Anime Assistant` 打开
- **移除 C_Cpp_Runner 本机配置**：`.vscode/settings.json` 与 `launch.json` 中的
  C_Cpp_Runner 条目（含机器特定编译器路径）从仓库删除，恢复项目可移植性
- **移除 helloWorld 命令**：扩展命令与对应测试一并删除
- **`.vscodeignore` 排除 `__pycache__`/`*.pyc`**：vsce 打包不再混入 Python 缓存文件
- README 对齐实际行为（Python ≥ 3.9、退出菜单、协议示例 language 字段、命令表、FAQ）

## [0.2.3] — 2026-09-16

### Fixed
- **错误类别误判**：type 关键词 `"is not"` 排在 name 之前，Python 的
  `name 'x' is not defined`（NameError）会被误判为 type_error，永远选不到
  "未定义变量"场景的台词。现在按"具体类别优先"重排（syntax → name → import → type），
  并补充 `convert` 关键词。12 条真实错误消息分类测试全部通过
- **watcher.py 单文件模式去重**：原来只比较错误数量，数量相同但错误内容变化时
  不再推送；改为与目录模式一致的内容签名去重（并保留启动时干净文件不打扰的行为）
- **ui.html 情绪回落不完整**：情绪 8 秒超时后 CSS 表情恢复 idle，但自定义立绘
  仍停留在情绪变体图片（如一直显示 character_angry.png），现在同时切回默认立绘
- **standalone.py 启动问候兜底**：Python 后端不可用时的问候气泡从 `...`
  换成有内容的台词（上一版只修了 /push 路径，漏了启动路径）

## [0.2.2] — 2026-09-14

### Fixed
- **all_clear 误报**（扩展 + watcher 双侧）：只要本次变化的文件没有错误就发"全部清零"，
  哪怕其他文件还有错误。现在扩展按工作区整体诊断判断，watcher 按文件缓存错误后汇总判断
- **watcher.py Python 错误解析**：单个语法错误被 stderr 逐行拆成 2~3 条"错误"导致计数虚高，
  现在只取异常摘要行（`SyntaxError: ...`），最多返回一条
- **watcher.py 语言 ID**：`.cpp/.cxx/.cc` 文件报错时 languageId 错标为 `c`，现在正确标为 `cpp`
- **standalone.py SSE 队列错位**：队列被裁剪后已连接客户端的索引失效，会丢消息或重发消息。
  改为每条消息带递增序号，客户端按序号取增量；新连接只重播最近 20 条（原来重播整个队列）
- **重复推送**：相同错误状态不再重复推送到桌宠（扩展侧和 watcher 侧都加了内容签名去重），
  Airi 不会就同一批错误反复吐槽；watcher 启动时项目本身干净也不再打断问候语
- **standalone.py**：`language` 字段现在透传给回复生成器（原来硬编码 'unknown'）；
  Python 后端不可用时的兜底气泡从 `...` 换成有意义的台词
- **package.json**：补上缺失的 `publisher` 字段（原来 `vsce package` 会直接失败）和 `repository`
- **extension.test.ts**：扩展 ID 写错（`Mr.hancard.*`），按 publisher 修正；
  新增 `launchPet` 命令注册测试

## [0.2.1] — 2026-09-13

### Added
- `Launch Airi Desktop Pet` VS Code 命令：在扩展内一键启动桌宠
  （自动查找 Python → 分离进程运行 `desktop_pet/standalone.py` → 探测 19876 端口确认启动成功）
- `checkStandaloneAlive()` 健康探测（15s 缓存）：桌宠服务器未运行时不再发送无意义请求
- 重建 `desktop_pet/common.py`（此前被误删且从未提交，导致 standalone.py / main.py
  启动即崩溃 `ModuleNotFoundError: No module named 'common'`）：
  - `get_screen_size()` — ctypes 物理像素（DPI aware），非 Windows 回退 tkinter
  - `WindowAPI` — pywebview js_api（拖拽移动 / 读取位置 / 隐藏窗口）
  - `find_character_image()` — 查找 assets/ 下的自定义立绘
  - `load_html()` — 注入 ui.html 的 `{{PORT}}` / `{{CHARACTER_IMAGE}}` 占位符
  - `create_temp_html()` — 写临时 HTML 并返回 file:// URL

### Fixed
- Webview 面板：错误信息先 HTML 转义再拼接，避免编译器错误中的 `<` `>` 等字符破坏显示
- 诊断清零时的 `all_clear` 消息只在桌宠服务器在线时发送
- `start.bat` / `launch_full.bat` 中文乱码（文件头加 `chcp 65001`）
- 删除误生成的垃圾文件 `desktop_pet/$null`

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
