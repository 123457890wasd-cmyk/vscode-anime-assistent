/*
 * ============================================================================
 * airi-monitor (vscode-anime-assistent) — VS Code 扩展入口文件
 * ============================================================================
 *
 * 【项目概述】
 * 这是一个 VS Code 扩展，名为 "airi-monitor"。它在 VS Code 启动后自动激活，
 * 监控 C/C++/Python 文件的编译诊断错误和编辑活动，将结果显示在自定义的
 * "Airi Assistant" Webview 面板中，同时将事件数据转发给 Python 后端进程。
 *
 * 【架构概览】
 *
 *   VS Code 启动
 *     → 加载 package.json（清单文件）
 *     → 触发 activationEvent: "onStartupFinished"
 *     → 加载 ./out/extension.js（本文件的编译产物）
 *     → 调用 activate(context)
 *         ├── 注册两个命令 (helloWorld, openAssistant)
 *         ├── 启动 Python 子进程 (python_backend/main.py)
 *         ├── 创建 Webview 面板 (Airi Assistant)
 *         ├── 监听 onDidChangeDiagnostics（诊断变化事件）
 *         ├── 监听 onDidChangeTextDocument（文本编辑事件）
 *         └── 扫描启动时已存在的诊断信息
 *
 * 【数据流】
 *
 *   VS Code 事件源                   目标
 *   ─────────────────────────────────────────────────
 *   诊断变化 (DiagnosticsChanged)  → Webview + Python
 *   文本编辑 (TextDocumentChange)  → Webview + Python
 *   Python stdout                  → Webview (+ 状态栏)
 *   Python stderr                  → Webview
 *   Webview requestSync            → Webview (sync 响应)
 *
 * 【消息协议】
 *   扩展与 Webview 之间通过 JSON 消息通信，每条消息包含 type 和 payload 字段。
 *   扩展与 Python 之间通过 stdin/stdout 以换行分隔的 JSON 行通信。
 *
 *   Extension → Webview:  diagnostics, heartbeat, pythonMessage, pythonText,
 *                          pythonError, backendExit, sync
 *   Extension → Python:    heartbeat, diagnostics
 *   Webview → Extension:   requestSync
 *   Python → Extension:    任意 JSON 对象（通过 stdout）
 *
 * 【模块级状态说明】
 *   以下变量使用模块级作用域（而非函数内局部变量），因为：
 *   - activate() 和 deactivate() 需要共享 Python 进程引用以便清理
 *   - 多个事件回调函数需要访问同一个 Webview 面板和心跳计数器
 *   - VS Code 扩展的 activate 函数只在激活时调用一次，模块级状态
 *     在扩展的整个生命周期内持续存在
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';

// ============================================================================
// 模块级状态（全局单例）
// ============================================================================

/** Python 子进程引用。用于在扩展停用时 kill 进程，以及通过 stdin 发送数据 */
let pyProcess: cp.ChildProcess | undefined;

/** 上一次诊断错误总数。用于检测错误是否已全部修复（all_clear） */
let lastErrorCount = 0;

/**
 * Python stdout 输出缓冲区。
 * 由于 Node.js 流的数据事件可能不会在 JSON 行边界上精确触发
 * （一个 chunk 可能包含多行或不完整的半行），
 * 我们需要累积数据并手动按换行符分割。
 */
let outputBuffer = '';

/** Webview 面板的单例引用。使用单例模式确保只有一个助手面板存在 */
let panel: vscode.WebviewPanel | undefined;

/**
 * 编辑器心跳计数器。每次在支持的文件类型中发生文本编辑时递增，
 * 用于追踪当前会话中的编辑次数。
 */
let heartbeatCounter = 0;

/**
 * 支持监控的语言 ID 集合。
 * 目前只监控 C（语言 ID: 'c'）、C++（'cpp'）和 Python（'python'）文件。
 * VS Code 的语言 ID 由已安装的语言扩展定义。
 */
const supportedLanguageIds = new Set(['c', 'cpp', 'python']);

// ============================================================================
// activate() — VS Code 扩展入口点
// ============================================================================

/**
 * VS Code 扩展激活时调用的入口函数。
 * 当 package.json 中声明的 activationEvent（onStartupFinished）触发时，
 * VS Code 会自动调用此函数。
 *
 * 此函数完成以下初始化工作：
 * 1. 注册两个用户命令（helloWorld 和 openAssistant）
 * 2. 启动 Python 后端子进程
 * 3. 创建并显示 Webview 助手面板
 * 4. 订阅诊断变化事件（编译错误/警告）
 * 5. 订阅文本编辑事件（编辑心跳）
 * 6. 扫描当前已存在的诊断信息
 * 7. 注册扩展停用时的清理回调
 *
 * @param context - VS Code 提供的扩展上下文，用于管理订阅和生命周期
 */
export function activate(context: vscode.ExtensionContext) {
	// --- 命令注册 ---

	// 注册 "Hello World" 命令：在右下角弹出一条信息提示
	const helloCommand = vscode.commands.registerCommand('vscode-anime-assistent.helloWorld', () => {
		vscode.window.showInformationMessage('Hello World from airi-monitor!');
	});

	// 注册 "Open Anime Assistant" 命令：打开或聚焦助手面板
	const openAssistantCommand = vscode.commands.registerCommand('vscode-anime-assistent.openAssistant', () => {
		createOrShowAssistantPanel(context);
	});

	// 将命令添加到 context.subscriptions，VS Code 会在扩展停用时自动注销
	context.subscriptions.push(helloCommand, openAssistantCommand);

	// --- Python 后端启动 ---
	startPythonBackend(context);

	// --- Webview 面板创建 ---
	createOrShowAssistantPanel(context);

	// --- 诊断变化事件监听 ---
	// 当 VS Code 的语言服务器/linter 检测到代码错误或警告时触发
	// event.uris 包含受影响的文件 URI 列表
	const diagnosticsDisposable = vscode.languages.onDidChangeDiagnostics((event) => {
		handleDiagnosticsChanged(event.uris);
	});
	context.subscriptions.push(diagnosticsDisposable);

	// --- 文本编辑事件监听（编辑心跳） ---
	// 每次用户在编辑器中修改文本时触发
	const editHeartbeatDisposable = vscode.workspace.onDidChangeTextDocument((event) => {
		const languageId = event.document.languageId;

		// 只处理受支持的语言（C/C++/Python），忽略其他文件类型
		if (!supportedLanguageIds.has(languageId)) {
			return;
		}

		// 递增心跳计数器，表示本会话中又发生了一次编辑
		heartbeatCounter += 1;

		// 构建心跳事件消息
		const payload = {
			type: 'heartbeat',
			payload: {
				editsInSession: heartbeatCounter,
				file: event.document.fileName,
				languageId,
				timestamp: new Date().toISOString()
			}
		};

		// 同时发送到 Webview 面板和 Python 后端
		postToWebview({
			type: 'heartbeat',
			payload: payload.payload
		});
		sendToPython(payload);
	});
	context.subscriptions.push(editHeartbeatDisposable);

	// --- 启动时扫描现有诊断 ---
	// 如果用户在扩展激活之前已经打开了有错误的文件，
	// 这些错误不会触发 onDidChangeDiagnostics 事件（因为没有"变化"），
	// 所以我们需要手动扫描一遍已有诊断
	for (const [uri, diagnostics] of vscode.languages.getDiagnostics()) {
		if (diagnostics.length > 0) {
			handleDiagnosticsChanged([uri]);
		}
	}

	// --- 注册清理回调 ---
	// 当扩展停用时，确保 Python 子进程被终止，避免僵尸进程
	context.subscriptions.push(
		new vscode.Disposable(() => {
			if (pyProcess && !pyProcess.killed) {
				pyProcess.kill();
			}
		})
	);
}

// ============================================================================
// deactivate() — VS Code 扩展停用回调
// ============================================================================

/**
 * VS Code 扩展停用时调用的清理函数。
 * 负责终止 Python 子进程，防止进程泄漏。
 * VS Code 会在扩展被禁用、VS Code 关闭或扩展重新加载时调用此函数。
 */
export function deactivate() {
	if (pyProcess && !pyProcess.killed) {
		pyProcess.kill();
	}
}

// ============================================================================
// startPythonBackend() — 启动 Python 后端子进程
// ============================================================================

/**
 * 启动 Python 后端子进程并建立双向通信管道。
 *
 * Python 进程运行 python_backend/main.py 脚本，通过以下方式与扩展通信：
 * - stdin：扩展向 Python 发送 JSON 消息（每行一个 JSON 对象）
 * - stdout：Python 向扩展返回 JSON 响应（每行一个 JSON 对象）
 * - stderr：Python 的错误输出，转发到 Webview 面板显示
 *
 * 【stdout 行缓冲处理】
 *   Node.js 的流数据事件以 Buffer chunk 为单位触发，不保证在换行符边界。
 *   因此使用 outputBuffer 累积数据，然后逐行提取完整的 JSON 行。
 *   这种方式称为"行分帧"（line framing）。
 *
 * @param context - VS Code 扩展上下文，用于获取扩展安装路径
 */
function startPythonBackend(context: vscode.ExtensionContext): void {
	const pythonPath = 'python';
	// 构造 Python 脚本的绝对路径：<扩展目录>/python_backend/main.py
	const scriptPath = path.join(context.extensionPath, 'python_backend', 'main.py');

	// 使用 child_process.spawn 启动子进程，'pipe' 选项启用 stdin/stdout/stderr 管道
	pyProcess = cp.spawn(pythonPath, [scriptPath], { stdio: 'pipe' });

	// 验证管道是否都成功建立。如果系统资源不足或配置问题，管道可能为 null
	if (!pyProcess.stdout || !pyProcess.stderr || !pyProcess.stdin) {
		vscode.window.showWarningMessage('Python backend started without full stdio pipes.');
		return;
	}

	// --- stdout 处理：逐行解析 JSON ---
	pyProcess.stdout.on('data', (chunk: Buffer) => {
		// 将新数据追加到缓冲区
		outputBuffer += chunk.toString();

		// 查找第一个换行符的位置
		let lineBreakIndex = outputBuffer.indexOf('\n');

		// 循环处理缓冲区中所有完整的行
		while (lineBreakIndex >= 0) {
			// 提取一行（去掉首尾空白）
			const line = outputBuffer.slice(0, lineBreakIndex).trim();
			// 从缓冲区中移除已处理的行
			outputBuffer = outputBuffer.slice(lineBreakIndex + 1);

			if (line.length > 0) {
				handlePythonLine(line);
			}

			// 检查是否还有更多完整的行
			lineBreakIndex = outputBuffer.indexOf('\n');
		}
	});

	// --- stderr 处理：转发错误输出到 Webview ---
	pyProcess.stderr.on('data', (chunk: Buffer) => {
		postToWebview({
			type: 'pythonError',
			payload: { message: chunk.toString() }
		});
	});

	// --- 进程错误处理：Python 无法启动时的提示 ---
	pyProcess.on('error', (error: Error) => {
		vscode.window.showErrorMessage(`Failed to start Python backend: ${error.message}`);
	});

	// --- 进程退出处理：通知 Webview 后端已退出 ---
	pyProcess.on('exit', (code, signal) => {
		postToWebview({
			type: 'backendExit',
			payload: { code, signal }
		});
	});
}

// ============================================================================
// handlePythonLine() — 处理 Python 后端返回的一行 JSON
// ============================================================================

/**
 * 解析 Python stdout 输出的一行数据。
 *
 * 如果该行是合法的 JSON，则：
 * - 作为 pythonMessage 发送到 Webview 面板
 * - 如果 JSON 中包含 message 字段，在 VS Code 状态栏显示 3 秒
 *
 * 如果该行不是合法的 JSON（例如 Python 的 print 调试输出），
 * 则作为 pythonText 原始文本发送到 Webview。
 *
 * @param line - 从 Python stdout 中提取的一行文本
 */
function handlePythonLine(line: string): void {
		try {
			const parsed = JSON.parse(line) as { type?: string; payload?: { message?: string; text?: string }; message?: string; [key: string]: unknown };

			// 根据消息类型路由到 Webview
			// chatMessage / errorAlert / statusChange → 直接转发
			// 其他 → 作为 pythonMessage 转发（兼容旧协议）
			const msgType = parsed.type;
			if (msgType === 'chatMessage' || msgType === 'errorAlert' || msgType === 'statusChange') {
				postToWebview(parsed);
			} else {
				postToWebview({
					type: 'pythonMessage',
					payload: parsed
				});
			}

			// 如果有文本消息，在状态栏短暂显示
			const displayText = parsed.payload?.text || parsed.payload?.message || parsed.message;
			if (typeof displayText === 'string' && displayText.length > 0) {
				vscode.window.setStatusBarMessage(`Airi: ${displayText}`, 3000);
			}
		} catch {
			postToWebview({
				type: 'pythonText',
				payload: { raw: line }
			});
		}
	}

// ============================================================================
// sendToPython() — 向 Python 后端发送 JSON 消息
// ============================================================================

/**
 * 将任意消息序列化为 JSON 并通过 stdin 发送给 Python 子进程。
 *
 * 消息以换行符 \n 结尾，Python 端的 sys.stdin 迭代器按行读取。
 * 如果 Python 进程不存在、已被终止或 stdin 不可用，则静默跳过。
 *
 * @param message - 要发送的数据，会被 JSON.stringify 序列化
 */
function sendToPython(message: unknown): void {
	// 安全检查：确保 Python 进程仍在运行且 stdin 可用
	if (!pyProcess || pyProcess.killed || !pyProcess.stdin) {
		return;
	}

	// 序列化为 JSON 并写入 stdin，以换行符分隔
	pyProcess.stdin.write(`${JSON.stringify(message)}\n`);
}

// ============================================================================
// handleDiagnosticsChanged() — 处理诊断变化事件
// ============================================================================

/**
 * 处理 VS Code 诊断（编译错误/警告）变化事件。
 *
 * 此函数的工作流程：
 * 1. 遍历受影响的 URI 列表
 * 2. 对每个 URI，查找对应的文本文档
 * 3. 过滤：只处理受支持语言（C/C++/Python）
 * 4. 过滤：只保留 Error 级别的诊断（忽略 Warning、Info、Hint）
 * 5. 标准化诊断代码（code 字段可能为 string、number 或对象）
 * 6. 构建结构化事件并发送到 Webview 和 Python 后端
 *
 * 【为什么只取 Error 级别？】
 *   警告和信息级别的诊断数量通常很大，过滤后减少噪音，
 *   让用户聚焦于真正需要修复的编译错误。
 *
 * @param uris - 诊断发生变化的文件 URI 列表
 */
function handleDiagnosticsChanged(uris: readonly vscode.Uri[]): void {
	// 使用 flatMap 遍历所有受影响的 URI，将每个文件的错误诊断展平为单一数组
	const errors = uris.flatMap((uri) => {
		// 通过 URI 查找对应的文本文档（需要文档已打开）
		const document = vscode.workspace.textDocuments.find((doc) => doc.uri.toString() === uri.toString());
		if (!document) {
			return []; // 文档未打开，跳过
		}

		// 只处理受支持的语言
		if (!supportedLanguageIds.has(document.languageId)) {
			return [];
		}

		// 获取该 URI 的所有诊断信息
		const diagnostics = vscode.languages.getDiagnostics(uri);

		return diagnostics
			// 只保留 Error 级别的诊断
			.filter((diagnostic) => diagnostic.severity === vscode.DiagnosticSeverity.Error)
			// 将 VS Code 的 Diagnostic 对象映射为自定义的错误描述结构
			.map((diagnostic) => ({
				file: uri.fsPath,
				languageId: document.languageId,
				message: diagnostic.message,
				source: diagnostic.source ?? 'unknown',  // 诊断来源（如 "eslint", "gcc"），默认为 "unknown"
				code: normalizeDiagnosticCode(diagnostic.code),
				// line 和 character 从 0 开始，+1 转换为人类习惯的从 1 开始
				line: diagnostic.range.start.line + 1,
				character: diagnostic.range.start.character + 1
			}));
	});

	// 如果没有错误，不发送空事件
	if (errors.length === 0) {
		return;
	}

	// 构建诊断事件
	const event = {
		type: 'diagnostics',
		payload: {
			count: errors.length,
			items: errors,
			timestamp: new Date().toISOString()
		}
	};

		// 发送给 Python 后端生成傲娇回复（Webview 由后端驱动）
		sendToPython(event);

		// --- 检测错误清零 (all_clear) ---
		// 扫描所有受支持文件的当前错误总数
		let totalErrors = 0;
		for (const doc of vscode.workspace.textDocuments) {
			if (supportedLanguageIds.has(doc.languageId)) {
				totalErrors += vscode.languages.getDiagnostics(doc.uri)
					.filter(d => d.severity === vscode.DiagnosticSeverity.Error).length;
			}
		}

		if (lastErrorCount > 0 && totalErrors === 0) {
			sendToPython({
				type: 'diagnostics',
				payload: {
					count: 0,
					items: [],
					language: 'unknown',
					files: [],
					trigger: 'all_clear',
					timestamp: new Date().toISOString()
				}
			});
		}

		lastErrorCount = totalErrors;
	}

// ============================================================================
// createOrShowAssistantPanel() — Webview 面板管理
// ============================================================================

/**
 * 创建或显示 "Airi Assistant" Webview 面板。
 *
 * 使用单例模式：如果面板已经存在，则聚焦显示（reveal）；
 * 如果不存在，则创建新面板。
 *
 * 【Webview 生命周期】
 *   - retainContextWhenHidden: true → 切换标签页时保留 Webview 状态，避免重新加载
 *   - enableScripts: true → 允许 Webview 内执行 JavaScript（用于消息通信）
 *   - onDidDispose → 用户关闭面板时，将 panel 设为 undefined，下次调用会创建新面板
 *
 * 【消息通信】
 *   panel.webview.onDidReceiveMessage 监听来自 Webview 的消息。
 *   目前支持的消息类型：
 *   - requestSync: Webview 加载完成后请求同步当前状态（心跳计数、后端状态）
 *
 * @param context - VS Code 扩展上下文
 */
function createOrShowAssistantPanel(context: vscode.ExtensionContext): void {
	// 单例检查：面板已存在则聚焦
	if (panel) {
		panel.reveal(vscode.ViewColumn.Beside);
		return;
	}

	// 创建新的 Webview 面板
	panel = vscode.window.createWebviewPanel(
		'airiAssistant',          // 面板类型标识符（唯一 ID）
		'Airi Assistant',          // 面板标题（显示在标签页上）
		vscode.ViewColumn.Beside,  // 在编辑器旁边打开（不占用当前编辑区）
		{
			enableScripts: true,           // 启用 JavaScript（postMessage 通信需要）
			retainContextWhenHidden: true  // 隐藏时不销毁，保留 Webview 状态
		}
	);

	// 设置 Webview 的 HTML 内容
	panel.webview.html = getWebviewHtml();

	// 监听面板销毁事件：用户关闭面板时，将单例引用置空
	panel.onDidDispose(() => {
		panel = undefined;
	});

	// 监听来自 Webview 的消息
	panel.webview.onDidReceiveMessage((message: { type?: string }) => {
		// Webview 加载完成后会发送 requestSync 请求同步状态
		if (message.type === 'requestSync') {
			postToWebview({
				type: 'sync',
				payload: {
					heartbeatCounter,
					backendReady: Boolean(pyProcess && !pyProcess.killed)
				}
			});
		}
	});

	// 将面板添加到 context.subscriptions，确保扩展停用时自动清理
	context.subscriptions.push(panel);
}

// ============================================================================
// postToWebview() — 向 Webview 发送消息
// ============================================================================

/**
 * 向 Webview 面板发送 JSON 消息。
 *
 * 如果面板不存在（用户关闭了面板或尚未创建），则静默跳过。
 * 使用 void 忽略 postMessage 返回的 Promise（消息投递是"发后即忘"模式）。
 *
 * @param message - 要发送的消息，会被序列化为 JSON 传递
 */
function postToWebview(message: unknown): void {
	if (!panel) {
		return;
	}

	void panel.webview.postMessage(message);
}

// ============================================================================
// normalizeDiagnosticCode() — 诊断代码标准化
// ============================================================================

/**
 * 标准化 VS Code 的诊断代码。
 *
 * VS Code 的 Diagnostic.code 字段有以下几种可能类型：
 * - string:      如 "no-unused-vars"（ESLint 规则名）
 * - number:      如 2322（TypeScript 错误代码）
 * - { value, target }: 对象形式，value 是实际代码，target 是文档链接 URI
 * - undefined:   无代码
 *
 * 此函数将对象形式解包为原始的 value 值，保持 string/number/undefined 不变。
 * 这样做是为了在 JSON 序列化时避免序列化复杂的嵌套对象。
 *
 * @param code - VS Code Diagnostic.code 的原始值
 * @returns 标准化后的代码值（string | number | undefined）
 */
function normalizeDiagnosticCode(
	code: string | number | { value: string | number; target: vscode.Uri } | undefined
): string | number | undefined {
	// 已经是原始类型，直接返回
	if (typeof code === 'string' || typeof code === 'number') {
		return code;
	}

	// 对象形式：提取 value 字段；如果 code 为 undefined，?.value 返回 undefined
	return code?.value;
}

// ============================================================================
// getWebviewHtml() — 生成 Webview 的 HTML 内容
// ============================================================================

/**
 * 生成 Webview 面板的完整 HTML 文档。
 *
 * 这是一个内联的单文件 HTML，包含 CSS 样式和 JavaScript 逻辑，
 * 不需要加载任何外部资源。
 *
 * 【UI 布局】
 *   两个卡片区域：
 *   1. 状态卡片（Airi Assistant）：
 *      - 标题栏
 *      - 状态文本（status）：显示当前状态提示
 *      - 气泡文本（bubble）：显示助手消息
 *   2. 诊断列表卡片（Recent Diagnostics）：
 *      - 标题栏
 *      - 可滚动的错误列表（最多显示 20 条）
 *
 * 【设计风格】
 *   - 暗色主题（深灰背景 + 琥珀色强调）
 *   - 径向渐变背景
 *   - 卡片式布局（圆角、半透明边框）
 *   - CSS 自定义属性（变量）管理颜色令牌
 *
 * 【JavaScript 逻辑】
 *   使用 VS Code Webview API (acquireVsCodeApi) 与扩展通信：
 *   - 接收消息：window.addEventListener('message', ...)
 *     处理 diagnostics, pythonMessage, heartbeat, backendExit, pythonError
 *   - 发送消息：vscode.postMessage(...)
 *     页面加载时发送 requestSync 请求同步初始状态
 *
 * 【renderDiagnostics 函数】
 *   将错误列表渲染到诊断卡片中：
 *   - 最多显示 20 条（slice(0, 20)）
 *   - 每条包含：错误消息文本 + 元信息（语言、文件路径、行列号）
 *
 * @returns 完整的 HTML 文档字符串
 */
function getWebviewHtml(): string {
	return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    :root {
      color-scheme: dark;
      --bg: #1a1a2e;
      --surface: #16213e;
      --bubble: #1f2b47;
      --text: #e8e8e8;
      --muted: #8888aa;
      --accent: #ff6b9d;
      --accent-dim: #c04a78;
      --error-border: #ff4757;
      --error-bubble: rgba(255,71,87,0.08);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    /* ===== 顶部状态栏 ===== */
    .topbar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      background: var(--surface);
      border-bottom: 1px solid rgba(255,255,255,0.06);
      flex-shrink: 0;
    }
    .topbar .name {
      font-size: 14px;
      font-weight: 700;
      color: var(--accent);
    }
    .topbar .dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #4ade80;
      box-shadow: 0 0 6px #4ade80;
      flex-shrink: 0;
    }
    .topbar .dot.offline { background: #666; box-shadow: none; }
    .topbar .status-text {
      font-size: 11px;
      color: var(--muted);
      margin-left: auto;
    }

    /* ===== 消息列表区 ===== */
    .chat {
      flex: 1;
      overflow-y: auto;
      padding: 12px 10px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .chat::-webkit-scrollbar { width: 4px; }
    .chat::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }

    /* ===== 系统消息（居中，小字） ===== */
    .sys-msg {
      text-align: center;
      font-size: 11px;
      color: var(--muted);
      padding: 4px 0;
      opacity: 0.7;
    }

    /* ===== 聊天气泡（Airi 消息，靠左） ===== */
    .msg-row {
      display: flex;
      align-items: flex-end;
      gap: 6px;
      animation: popIn 0.3s ease-out;
    }
    @keyframes popIn {
      from { opacity: 0; transform: translateY(8px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .msg-row .avatar-placeholder {
      width: 28px; height: 28px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--accent), var(--accent-dim));
      flex-shrink: 0;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .bubble {
      max-width: 82%;
      padding: 10px 14px;
      border-radius: 16px 16px 16px 4px;
      background: var(--bubble);
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }
    /* 错误类气泡：左边粉红边框 */
    .bubble.error {
      border-left: 3px solid var(--error-border);
      background: var(--error-bubble);
    }
    .bubble .ts {
      font-size: 10px;
      color: var(--muted);
      display: block;
      margin-top: 4px;
    }

    /* ===== 打字指示器 ===== */
    .typing {
      display: none;
      align-items: center;
      gap: 6px;
      padding: 4px 0 8px 34px;
    }
    .typing.show { display: flex; }
    .typing span {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: var(--muted);
      animation: bounce 1.2s infinite ease-in-out;
    }
    .typing span:nth-child(2) { animation-delay: 0.2s; }
    .typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes bounce {
      0%, 80%, 100% { transform: translateY(0); }
      40% { transform: translateY(-6px); }
    }
  </style>
</head>
<body>
  <!-- 顶部状态栏 -->
  <div class="topbar">
    <span class="name">Airi</span>
    <span class="dot" id="dot"></span>
    <span class="status-text" id="statusLabel">online</span>
  </div>

  <!-- 聊天消息区 -->
  <div class="chat" id="chat">
    <div class="sys-msg">— Airi 已上线 —</div>
  </div>

  <!-- 打字指示器 -->
  <div class="typing" id="typing">
    <span></span><span></span><span></span>
  </div>

  <script>
    const vscode = acquireVsCodeApi();
    const chatEl = document.getElementById('chat');
    const dotEl = document.getElementById('dot');
    const statusLabel = document.getElementById('statusLabel');
    const typingEl = document.getElementById('typing');

    function scrollBottom() {
      chatEl.scrollTop = chatEl.scrollHeight;
    }

    /** 追加一条系统消息 */
    function addSysMsg(text) {
      const el = document.createElement('div');
      el.className = 'sys-msg';
      el.textContent = text;
      chatEl.appendChild(el);
      scrollBottom();
    }

    /** 追加一条 Airi 聊天气泡 */
    function addBubble(text, isError) {
      const row = document.createElement('div');
      row.className = 'msg-row';

      const avatar = document.createElement('div');
      avatar.className = 'avatar-placeholder';
      avatar.textContent = 'A';

      const bubble = document.createElement('div');
      bubble.className = isError ? 'bubble error' : 'bubble';
      bubble.textContent = text;

      const ts = document.createElement('span');
      ts.className = 'ts';
      ts.textContent = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
      bubble.appendChild(ts);

      row.appendChild(avatar);
      row.appendChild(bubble);
      chatEl.appendChild(row);
      scrollBottom();
    }

    /** 显示/隐藏打字指示器 */
    function showTyping(show) {
      typingEl.classList.toggle('show', show);
      if (show) scrollBottom();
    }

    /** 更新在线状态 */
    function setOnline(online) {
      dotEl.classList.toggle('offline', !online);
      statusLabel.textContent = online ? 'online' : 'offline';
    }

    window.addEventListener('message', function (event) {
      const msg = event.data;
      if (!msg || !msg.type) return;

      switch (msg.type) {

        // --- Airi 聊天消息 ---
        case 'chatMessage':
          addBubble(msg.payload.text || '', false);
          break;

        // --- 错误提醒（特殊气泡样式） ---
        case 'errorAlert':
          addBubble(msg.payload.text || '', true);
          break;

        // --- 状态变更 ---
        case 'statusChange':
          addSysMsg(msg.payload.text || '');
          break;

        // --- 后端退出 ---
        case 'backendExit':
          setOnline(false);
          addSysMsg('— Airi 已离线 —');
          break;

        // --- Python 原始输出（兼容旧消息） ---
        case 'pythonMessage':
          if (msg.payload && msg.payload.message) {
            addBubble(msg.payload.message, false);
          }
          break;

        // --- 诊断消息（由后端处理后下发聊天消息） ---
        case 'diagnostics':
          break;

        // --- 心跳（静默） ---
        case 'heartbeat':
          break;

        // --- Python 错误输出 ---
        case 'pythonError':
          addSysMsg('[stderr] ' + (msg.payload?.message || ''));
          break;

        // --- 同步响应 ---
        case 'sync':
          setOnline(msg.payload?.backendReady || false);
          break;
      }
    });

    vscode.postMessage({ type: 'requestSync' });
  </script>
</body>
</html>`;
}
