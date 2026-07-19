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
 *         ├── 启动 HTTP SSE 服务器 (桌面宠物通信)
 *         ├── 启动桌面宠物窗口 (desktop_pet/main.py)
 *         ├── 监听 onDidChangeDiagnostics（诊断变化事件）
 *         ├── 监听 onDidChangeTextDocument（文本编辑事件）
 *         └── 扫描启动时已存在的诊断信息
 *
 * 【数据流】
 *
 *   VS Code 事件源                   目标
 *   ─────────────────────────────────────────────────
 *   诊断变化 (DiagnosticsChanged)  → Webview + Python + 桌面宠物
 *   文本编辑 (TextDocumentChange)  → Webview + Python
 *   Python stdout                  → Webview + 状态栏 + 桌面宠物(SSE)
 *   Python stderr                  → Webview
 *   Webview requestSync            → Webview (sync 响应)
 *   桌面宠物 SSE 连接              → HTTP SSE 推送
 *
 * 【消息协议】
 *   扩展与 Webview 之间通过 JSON 消息通信，每条消息包含 type 和 payload 字段。
 *   扩展与 Python 之间通过 stdin/stdout 以换行分隔的 JSON 行通信。
 *   扩展与桌面宠物之间通过 HTTP SSE（Server-Sent Events）通信。
 *
 *   Extension → Webview:  diagnostics, heartbeat, pythonMessage, pythonText,
 *                          pythonError, backendExit, sync
 *   Extension → Python:    heartbeat, diagnostics
 *   Extension → Desktop:   chatMessage, errorAlert, statusChange, backendExit (SSE)
 *   Webview → Extension:   requestSync
 *   Python → Extension:    任意 JSON 对象（通过 stdout）
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as http from 'http';

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
// 桌面宠物相关状态
// ============================================================================

/** HTTP 服务器，用于向桌面宠物窗口推送消息（SSE） */
let httpServer: http.Server | undefined;

/** SSE 客户端列表（桌面宠物窗口 EventSource 连接） */
const sseClients: http.ServerResponse[] = [];

/** 桌面宠物 Python 子进程引用 */
let desktopPetProcess: cp.ChildProcess | undefined;

/** HTTP 服务器实际监听的端口号（由 listen(0) 自动分配） */
let desktopPort = 0;

/** 扩展安装路径（模块级缓存，避免各处传递 context） */
let extPath = '';

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
 * 3. 启动 HTTP SSE 服务器（桌面宠物通信）
 * 4. 启动桌面宠物窗口
 * 5. 创建并显示 Webview 助手面板
 * 6. 订阅诊断变化事件（编译错误/警告）
 * 7. 订阅文本编辑事件（编辑心跳）
 * 8. 扫描当前已存在的诊断信息
 * 9. 注册扩展停用时的清理回调
 *
 * @param context - VS Code 提供的扩展上下文，用于管理订阅和生命周期
 */
export function activate(context: vscode.ExtensionContext) {
	// 诊断：扩展激活入口
	console.log('[airi-monitor] activate() called, extensionPath:', context.extensionPath);
	vscode.window.showInformationMessage('Airi Monitor 正在启动…');

	// --- 命令注册 ---

	// 注册 "Hello World" 命令：在右下角弹出一条信息提示
	const helloCommand = vscode.commands.registerCommand('vscode-anime-assistent.helloWorld', () => {
		vscode.window.showInformationMessage('Hello World from airi-monitor!');
	});

	// 注册 "Open Anime Assistant" 命令：打开或聚焦助手面板
	const openAssistantCommand = vscode.commands.registerCommand('vscode-anime-assistent.openAssistant', () => {
		createOrShowAssistantPanel(context);
	});

	// 注册 "Toggle Desktop Pet" 命令：开关桌面宠物窗口
	const toggleDesktopPetCommand = vscode.commands.registerCommand('vscode-anime-assistent.toggleDesktopPet', () => {
		if (desktopPetProcess && !desktopPetProcess.killed) {
			killDesktopPet();
			vscode.window.showInformationMessage('Airi 桌面宠物已隐藏');
		} else {
			spawnDesktopPet();
			vscode.window.showInformationMessage('Airi 桌面宠物已召唤');
		}
	});

	// 将命令添加到 context.subscriptions，VS Code 会在扩展停用时自动注销
	context.subscriptions.push(helloCommand, openAssistantCommand, toggleDesktopPetCommand);

	// --- 缓存扩展路径（供整个生命周期使用，必须在任何异步回调之前设置） ---
	extPath = context.extensionPath;

	// --- Python 后端启动 ---
	try {
		startPythonBackend(context);
	} catch (err) {
		console.error('[airi-monitor] Failed to start Python backend:', err);
		vscode.window.showErrorMessage(`Airi Python 后端启动失败: ${err}`);
	}

	// --- 检测 standalone 是否已在运行 ---
	checkStandalone((standaloneRunning) => {
		if (standaloneRunning) {
			console.log('[airi-monitor] Standalone server detected — skipping built-in desktop pet');
			vscode.window.showInformationMessage('Airi 检测到桌宠已在运行，诊断消息将自动推送');
		} else {
			// standalone 未运行，启动内置 HTTP 服务器和桌面宠物
			try {
				startDesktopServer();
			} catch (err) {
				console.error('[airi-monitor] Failed to start desktop server:', err);
			}
		}
	});

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
	// 当扩展停用时，确保 Python 子进程和桌面宠物进程被终止，HTTP 服务器关闭
	context.subscriptions.push(
		new vscode.Disposable(() => {
			killDesktopPet();
			if (httpServer) {
				httpServer.close();
				httpServer = undefined;
			}
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
 * 负责终止 Python 子进程和桌面宠物进程，关闭 HTTP 服务器，防止进程泄漏。
 * VS Code 会在扩展被禁用、VS Code 关闭或扩展重新加载时调用此函数。
 */
export function deactivate() {
	killDesktopPet();
	if (httpServer) {
		httpServer.close();
		httpServer = undefined;
	}
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
function resolvePythonPath(): string {
    // 尝试系统可用的 Python
    const candidates = [
        process.env.AIRI_PYTHON_PATH || '',
        'C:\\Users\\Mr.hancard\\AppData\\Local\\Programs\\Python\\Python312\\python.exe',
        'python',
        'python3',
    ];
    for (const p of candidates) {
        if (!p) continue;
        try {
            if (p === 'python' || p === 'python3') { return p; }
            if (fs.existsSync(p)) { return p; }
        } catch { /* continue */ }
    }
    return 'python';
}

function startPythonBackend(context: vscode.ExtensionContext): void {
	// 优先使用 Python 3.12（pywebview 已安装在此环境），fallback 到系统 python
	const pythonPath = process.env.AIRI_PYTHON_PATH || resolvePythonPath();
	// 构造 Python 脚本的绝对路径：<扩展目录>/python_backend/main.py
	const scriptPath = path.join(context.extensionPath, 'python_backend', 'main.py');

	// 使用 child_process.spawn 启动子进程，'pipe' 选项启用 stdin/stdout/stderr 管道
	pyProcess = cp.spawn(pythonPath, [scriptPath], {
		stdio: 'pipe',
		env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
	});

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

	// --- stderr 处理：转发错误输出到 Webview 并记录到控制台 ---
	pyProcess.stderr.on('data', (chunk: Buffer) => {
		const errMsg = chunk.toString();
		console.error('[airi-monitor] Python backend stderr:', errMsg);
		postToWebview({
			type: 'pythonError',
			payload: { message: errMsg }
		});
	});

	// --- 进程错误处理：Python 无法启动时的提示 ---
	pyProcess.on('error', (error: Error) => {
		vscode.window.showErrorMessage(`Failed to start Python backend: ${error.message}`);
	});

	// --- 进程退出处理：通知 Webview 和桌面宠物后端已退出 ---
	pyProcess.on('exit', (code, signal) => {
		const msg = {
			type: 'backendExit',
			payload: { code, signal }
		};
		postToWebview(msg);
		broadcastToDesktop(msg);
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
 * - 同步广播到桌面宠物窗口（SSE）
 * - 如果 JSON 中包含 message 字段，在 VS Code 状态栏显示 3 秒
 *
 * 如果该行不是合法的 JSON（例如 Python 的 print 调试输出），
 * 则作为 pythonText 原始文本发送到 Webview。
 *
 * @param line - 从 Python stdout 中提取的一行文本
 */
function handlePythonLine(line: string): void {
		try {
			const parsed = JSON.parse(line) as { type?: string; payload?: { message?: string; text?: string; [key: string]: unknown }; message?: string; [key: string]: unknown };

			// 根据消息类型路由到 Webview 和桌面宠物
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

			// 广播到桌面宠物窗口（SSE）
			broadcastToDesktop(parsed);

			// 推送到独立服务器（standalone.py），使双击启动的桌宠也能接收消息
			pushToStandalone(parsed);

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

	// 构建诊断事件负载
	const diagnosticsPayload = {
		type: 'diagnostics',
		payload: {
			count: errors.length,
			items: errors,
			timestamp: new Date().toISOString()
		}
	};

	// 发送到 Webview 面板（显示错误列表和气泡）
	postToWebview(diagnosticsPayload);

	// 只有当存在错误时才发送到 Python 后端（减少不必要的处理）
	if (errors.length > 0) {
		sendToPython(diagnosticsPayload);
		// 同时发送原始诊断数据到 standalone 服务器（即使 Python 后端挂了也能工作）
		pushToStandalone(diagnosticsPayload);
	}

	// --- all_clear 检测（错误清零） ---
	// 如果上次有错误，这次没有了 → 说明程序员修好了所有错误
	if (lastErrorCount > 0 && errors.length === 0) {
		// 构造 all_clear 上下文并发送到 Python 后端
		sendToPython({
			type: 'diagnostics',
			payload: {
				count: 0,
				items: [],
				timestamp: new Date().toISOString()
			}
		});

		// 同时发送 trigger 标记，让 Python 知道这是 all_clear 事件
		sendToPython({
			trigger: 'all_clear',
			error_count: 0,
			language: 'unknown',
			files: [],
			sample_errors: []
		});
		// 也推送到 standalone（格式：trigger=all_clear）
		pushToStandalone({
			trigger: 'all_clear',
			error_count: 0,
			language: 'unknown',
			files: [],
			sample_errors: []
		});
	}

	// 更新缓存的上次错误数量
	lastErrorCount = errors.length;
}

// ============================================================================
// createOrShowAssistantPanel() — 创建/显示助手面板
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
	        // 尝试加载角色立绘作为头像
        let avatarUri = '';
        try {
            const charImgPath = path.join(context.extensionPath, 'desktop_pet', 'assets', 'character.png');
            if (fs.existsSync(charImgPath)) {
                avatarUri = panel.webview.asWebviewUri(vscode.Uri.file(charImgPath)).toString();
            }
        } catch (e) {
            // 静默降级，使用文字头像
        }
        panel.webview.html = getWebviewHtml(avatarUri);

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
 * @param avatarUri - (可选) 角色图像的 webview URI，如果提供则替换文字头像
 * @returns 完整的 HTML 文档字符串
 */
function getWebviewHtml(avatarUri?: string): string {
		return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    :root {
      color-scheme: dark;
      --bg: #1a1a2e;
      --text: #e8e8e8;
      --muted: #8888aa;
      --accent: #ff6b9d;
      --accent-dim: #c04a78;
      --error-bg: rgba(255,71,87,0.12);
      --error-border: #ff4757;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-end;
      overflow: hidden;
      padding: 8px;
    }

    /* ===== 气泡区域 ===== */
    .bubble-area {
      width: 100%;
      max-width: 280px;
      display: flex;
      flex-direction: column-reverse;
      align-items: center;
      gap: 6px;
      margin-bottom: 10px;
      min-height: 0;
    }

    /* ===== 聊天气泡（QQ桌宠风格） ===== */
    .bubble {
      position: relative;
      max-width: 100%;
      padding: 10px 14px;
      border-radius: 16px;
      background: #1f2b47;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
      text-align: center;
      animation: popUp 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
      box-shadow: 0 2px 12px rgba(0,0,0,0.3);
    }
    /* 气泡下方的小三角 */
    .bubble::after {
      content: '';
      position: absolute;
      bottom: -8px;
      left: 50%;
      transform: translateX(-50%);
      width: 0; height: 0;
      border-left: 8px solid transparent;
      border-right: 8px solid transparent;
      border-top: 8px solid #1f2b47;
    }

    /* 错误气泡 */
    .bubble.error {
      background: var(--error-bg);
      border: 1px solid var(--error-border);
      box-shadow: 0 2px 16px rgba(255,71,87,0.2);
    }
    .bubble.error::after {
      border-top-color: var(--error-border);
    }

    /* 旧气泡半透明 */
    .bubble.old {
      opacity: 0.55;
      transform: scale(0.9);
      transition: all 0.4s ease;
    }

    @keyframes popUp {
      from { opacity: 0; transform: translateY(12px) scale(0.85); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }

    /* ===== 桌宠角色区 ===== */
    .pet-area {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      cursor: default;
      user-select: none;
    }
    /* 头像：圆形，支持自定义图片 */
    .avatar {
      width: 64px; height: 64px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--accent), var(--accent-dim));
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      font-weight: 700;
      color: #fff;
      box-shadow: 0 0 24px rgba(255,107,157,0.35);
      transition: transform 0.2s ease;
      animation: idleBounce 3s infinite ease-in-out;
      overflow: hidden;
      border: 2px solid var(--accent);
    }
    /* 当设置了自定义图片时使用 img 标签 */
    .avatar img {
      width: 100%; height: 100%;
      object-fit: cover;
      border-radius: 50%;
    }
    .avatar:active {
      transform: scale(0.9);
    }
    @keyframes idleBounce {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-5px); }
    }
    .pet-name {
      font-size: 12px;
      font-weight: 600;
      color: var(--accent);
      letter-spacing: 2px;
    }
    .pet-status {
      font-size: 10px;
      color: var(--muted);
    }
    .pet-status .dot {
      display: inline-block;
      width: 6px; height: 6px;
      border-radius: 50%;
      background: #4ade80;
      margin-right: 4px;
      vertical-align: middle;
    }
    .pet-status .dot.offline { background: #666; }

    /* ===== 打字动画 ===== */
    .typing-dots {
      display: none;
      gap: 4px;
      padding: 4px 0;
      justify-content: center;
    }
    .typing-dots.show { display: flex; }
    .typing-dots span {
      width: 5px; height: 5px;
      border-radius: 50%;
      background: var(--muted);
      animation: dotBounce 1.2s infinite ease-in-out;
    }
    .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
    .typing-dots span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes dotBounce {
      0%, 80%, 100% { transform: translateY(0); }
      40% { transform: translateY(-5px); }
    }
  </style>
</head>
<body>

  <!-- 气泡展示区 -->
  <div class="bubble-area" id="bubbleArea"></div>

  <!-- 打字动画 -->
  <div class="typing-dots" id="typing">
    <span></span><span></span><span></span>
  </div>

  <!-- 桌宠角色 -->
  <div class="pet-area">
        <div class="avatar" id="avatar" title="戳戳 Airi">
      <!-- 优先显示角色立绘，无图片时显示文字头像 -->
      <img id="avatarImg" src="" alt="Airi" style="display:none" />
      <span id="avatarText">A</span>
    </div>
    <div class="pet-name">Airi</div>
    <div class="pet-status">
      <span class="dot" id="dot"></span><span id="statusLabel">online</span>
    </div>
  </div>

  <script>
    const vscode = acquireVsCodeApi();
    const bubbleArea = document.getElementById('bubbleArea');
    const typingEl = document.getElementById('typing');
    const dotEl = document.getElementById('dot');
    const statusLabel = document.getElementById('statusLabel');
    const avatarEl = document.getElementById('avatar');

    const MAX_BUBBLES = 3;

    /** 移除最旧的气泡 */
    function pruneBubbles() {
      const bubbles = bubbleArea.querySelectorAll('.bubble');
      if (bubbles.length >= MAX_BUBBLES) {
        const oldest = bubbles[0];
        oldest.style.opacity = '0';
        oldest.style.transform = 'translateY(-10px) scale(0.8)';
        oldest.style.transition = 'all 0.3s ease';
        setTimeout(function() { oldest.remove(); }, 300);
      }
      if (bubbles.length >= 2) {
        bubbles[bubbles.length - 2].classList.add('old');
      }
    }

    /** 弹出新气泡 */
    function showBubble(text, isError) {
      pruneBubbles();
      var el = document.createElement('div');
      el.className = isError ? 'bubble error' : 'bubble';
      el.textContent = text;
      bubbleArea.appendChild(el);

      // 轻推一下桌宠
      avatarEl.style.animation = 'none';
      avatarEl.offsetHeight;
      avatarEl.style.animation = 'idleBounce 3s infinite ease-in-out';
    }

    /** 显示/隐藏打字指示器 */
    function showTyping(show) {
      typingEl.classList.toggle('show', show);
    }

    /** 更新在线状态 */
    function setOnline(online) {
      dotEl.classList.toggle('offline', !online);
      statusLabel.textContent = online ? 'online' : 'offline';
    }

    window.addEventListener('message', function (event) {
      var msg = event.data;
      if (!msg || !msg.type) return;

      switch (msg.type) {
        case 'chatMessage':
          showBubble(msg.payload.text || '', false);
          break;

        case 'errorAlert':
          showBubble(msg.payload.text || '', true);
          break;

        case 'statusChange':
          break;

        case 'backendExit':
          setOnline(false);
          showBubble('Airi \u5df2\u79bb\u7ebf\u2026', true);
          break;

        case 'pythonMessage':
          if (msg.payload && msg.payload.message) {
            showBubble(msg.payload.message, false);
          }
          break;

        case 'diagnostics':
        case 'heartbeat':
          break;

        case 'pythonError':
          break;

        case 'sync':
          setOnline(msg.payload?.backendReady || false);
          break;
      }
    });

    // 戳桌宠时回弹动画
    avatarEl.addEventListener('click', function() {
      avatarEl.style.transform = 'scale(0.85)';
      setTimeout(function() { avatarEl.style.transform = 'scale(1)'; }, 120);
    });

        // 加载角色立绘（webview URI）
    (function() {
      var AVATAR_URI = "${avatarUri || ""}";
      if (AVATAR_URI && AVATAR_URI.length > 0) {
        var img = document.getElementById('avatarImg');
        var txt = document.getElementById('avatarText');
        img.onload = function() { img.style.display = 'block'; txt.style.display = 'none'; };
        img.onerror = function() { img.style.display = 'none'; txt.style.display = 'inline'; };
        img.src = AVATAR_URI;
      }
    })();

    vscode.postMessage({ type: 'requestSync' });
  </script>
</body>
</html>`;
	}

// ============================================================================
// startDesktopServer() — 启动 HTTP SSE 服务器
// ============================================================================

/**
 * 启动本地 HTTP 服务器，用于向桌面宠物窗口推送消息。
 *
 * 使用 Server-Sent Events (SSE) 协议：
 * - GET /events → SSE 事件流，桌面宠物通过 EventSource 连接
 * - GET /ping  → 健康检查，返回 {"status":"ok"}
 * - POST /event → 接收桌面宠物发来的事件
 *
 * 服务器仅监听 127.0.0.1（localhost），不接受外部连接。
 * 端口由 listen(0) 自动分配，避免冲突。
 */
function startDesktopServer(): void {
	httpServer = http.createServer((req, res) => {
		// CORS 头，允许本地 pywebview 窗口跨域访问
		res.setHeader('Access-Control-Allow-Origin', '*');
		res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
		res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

		// 预检请求
		if (req.method === 'OPTIONS') {
			res.writeHead(204);
			res.end();
			return;
		}

		if (req.url === '/events' && req.method === 'GET') {
			// SSE 端点：保持长连接，推送事件
			res.writeHead(200, {
				'Content-Type': 'text/event-stream',
				'Cache-Control': 'no-cache',
				'Connection': 'keep-alive',
			});
			// 发送初始注释，确认连接成功
			res.write(':ok\n\n');

			sseClients.push(res);

			// 客户端断开时清理
			req.on('close', () => {
				const idx = sseClients.indexOf(res);
				if (idx >= 0) {
					sseClients.splice(idx, 1);
				}
			});
		} else if (req.url === '/ping' && req.method === 'GET') {
			// 健康检查
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ status: 'ok', port: desktopPort }));
		} else if (req.url === '/event' && req.method === 'POST') {
			// 接收桌面宠物发来的事件
			let body = '';
			req.on('data', (chunk: Buffer) => { body += chunk.toString(); });
			req.on('end', () => {
				try {
					const msg = JSON.parse(body);
					// 桌面宠物就绪通知
					if (msg.type === 'desktopReady') {
						console.log('[airi-monitor] Desktop pet connected');
					}
				} catch { /* 忽略无效 JSON */ }
				res.writeHead(200);
				res.end();
			});
		} else {
			console.log(`[airi-monitor] 404 Not Found: ${req.method} ${req.url}`);
			res.writeHead(404);
			res.end('404 Not Found');
		}
	});

	// 监听随机端口，就绪后自动启动桌面宠物
	httpServer.listen(0, '127.0.0.1', () => {
		const addr = httpServer!.address();
		if (addr && typeof addr === 'object') {
			desktopPort = addr.port;
			console.log(`[airi-monitor] Desktop server listening on http://127.0.0.1:${desktopPort}`);
			// 端口已分配，可以安全启动桌面宠物
			try {
				spawnDesktopPet();
				vscode.window.showInformationMessage(`Airi 桌宠已启动 (端口 ${desktopPort})`);
			} catch (err) {
				console.error('[airi-monitor] Failed to spawn desktop pet:', err);
			}
		}

		// SSE keepalive：每 20 秒发送心跳注释，防止代理/浏览器断开空闲连接
		// （放在 listen 回调内，避免 listen 失败时定时器泄漏）
		const keepaliveInterval = setInterval(() => {
			if (sseClients.length === 0) { return; }
			for (const client of [...sseClients]) {
				try { client.write(':ping\n\n'); } catch { /* close 事件会清理 */ }
			}
		}, 20000);

		// 服务器关闭时清除定时器
		httpServer!.on('close', () => {
			clearInterval(keepaliveInterval);
		});
	});
}

// ============================================================================
// broadcastToDesktop() — 向所有桌面宠物 SSE 客户端广播消息
// ============================================================================

/**
 * 将消息广播到所有已连接的桌面宠物窗口（SSE 客户端）。
 *
 * 消息以标准 SSE 格式发送：`data: <JSON>\n\n`
 * 如果没有任何客户端连接，则静默跳过。
 * 写入失败（如客户端已断开但尚未清理）会被静默捕获。
 *
 * @param message - 要广播的消息对象
 */
function broadcastToDesktop(message: unknown): void {
	if (sseClients.length === 0) {
		return;
	}

	const data = `data: ${JSON.stringify(message)}\n\n`;

	// 遍历副本，避免在迭代过程中因 close 事件修改数组
	for (const client of [...sseClients]) {
		try {
			client.write(data);
		} catch {
			// 写入失败（客户端已断开），close 事件会自行清理
		}
	}
}

// ============================================================================
// pushToStandalone() — 向独立服务器推送消息（VS Code 对接）
// ============================================================================

/**
 * 检测 standalone.py 服务器是否在运行（通过 ping localhost:19876）
 * @param callback — 接收 boolean 结果
 */
function checkStandalone(callback: (running: boolean) => void): void {
	const req = http.request(
		{ hostname: '127.0.0.1', port: 19876, path: '/ping', method: 'GET', timeout: 1000 },
		(res) => {
			let body = '';
			res.on('data', (c: Buffer) => body += c.toString());
			res.on('end', () => {
				try {
					const data = JSON.parse(body);
					callback(data.status === 'ok');
				} catch { callback(false); }
			});
		}
	);
	req.on('error', () => callback(false));
	req.on('timeout', () => { req.destroy(); callback(false); });
	req.end();
}

/**
 * 向 standalone.py 启动的独立 HTTP 服务器推送消息。
 *
 * 独立服务器监听 http://127.0.0.1:19876/push，
 * 如果服务器未运行则静默跳过（POST 请求失败不影响任何功能）。
 *
 * 这使得：双击 launch.bat 启动桌宠 → F5 启动扩展 → 诊断消息自动推送。
 *
 * @param message - 要推送的消息对象
 */
function pushToStandalone(message: unknown): void {
	try {
		const body = JSON.stringify(message);
		const req = http.request(
			{
				hostname: '127.0.0.1',
				port: 19876,
				path: '/push',
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					'Content-Length': Buffer.byteLength(body),
				},
				timeout: 500, // 500ms 超时，不阻塞
			},
			() => { /* 忽略响应 */ }
		);
		req.on('error', () => { /* 服务器未运行，静默跳过 */ });
		req.on('timeout', () => { req.destroy(); });
		req.write(body);
		req.end();
	} catch {
		/* 静默失败 */
	}
}
// ============================================================================

/**
 * 启动桌面宠物 Python 进程。
 *
 * 桌面宠物使用 pywebview 创建透明无边框窗口，通过 SSE 接收消息。
 * 进程启动时传入端口号作为命令行参数。
 *
 * 注意：必须在 startDesktopServer 的 listen 回调中调用，
 * 以确保 desktopPort 已分配。直接调用时如果端口未就绪会静默跳过。
 *
 * 如果进程已在运行（未 killed），则不会重复启动。
 * 启动失败时静默降级：不影响 VS Code 插件正常功能。
 */
function spawnDesktopPet(): void {
	// 避免重复启动
	if (desktopPetProcess && !desktopPetProcess.killed) {
		return;
	}

	// 端口必须已分配（由 startDesktopServer 的 listen 回调保证）
	if (desktopPort === 0) {
		console.log('[airi-monitor] Desktop pet: port not ready yet, skipping');
		return;
	}

	// 构造脚本路径（使用模块级缓存的扩展路径，不依赖 context 参数）
	const scriptDir = path.join(extPath, 'desktop_pet');
	const scriptPath = path.join(scriptDir, 'main.py');

	const pythonPath = process.env.AIRI_PYTHON_PATH || resolvePythonPath();

	try {
		desktopPetProcess = cp.spawn(pythonPath, [scriptPath, '--port', String(desktopPort)], {
			cwd: scriptDir,
			stdio: 'pipe',
			env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
			// 作为 VS Code 子进程运行，扩展停用时由 deactivate() 负责 kill
		});

		desktopPetProcess.on('error', () => {
			// Python 或 pywebview 不可用时静默降级
			// VS Code Webview 面板仍然可用
			console.log('[airi-monitor] Desktop pet failed to start (Python/pywebview not available)');
			desktopPetProcess = undefined;
		});

		desktopPetProcess.on('exit', (code) => {
			console.log(`[airi-monitor] Desktop pet exited with code ${code}`);
			desktopPetProcess = undefined;
		});

		// 把 stderr 输出到 VS Code 控制台便于调试
		if (desktopPetProcess.stderr) {
			desktopPetProcess.stderr.on('data', (chunk: Buffer) => {
				console.log(`[airi-monitor] Desktop pet stderr: ${chunk.toString()}`);
			});
		}
	} catch {
		// cp.spawn 可能抛出异常（如路径不存在）
		console.log('[airi-monitor] Desktop pet spawn failed');
		desktopPetProcess = undefined;
	}
}

// ============================================================================
// killDesktopPet() — 终止桌面宠物窗口进程
// ============================================================================

/**
 * 终止桌面宠物 Python 进程。
 *
 * 发送 SIGTERM 信号，让 pywebview 窗口正常关闭。
 * 如果进程不存在或已终止，则静默跳过。
 */
function killDesktopPet(): void {
	if (desktopPetProcess && !desktopPetProcess.killed) {
		desktopPetProcess.kill();
		desktopPetProcess = undefined;
	}
}
