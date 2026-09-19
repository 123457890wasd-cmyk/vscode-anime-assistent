/*
 * ============================================================================
 * airi-monitor (vscode-anime-assistent) — VS Code 扩展入口文件 (v0.2.0 精简版)
 * ============================================================================
 *
 * 【职责】
 * 轻量诊断桥接器：监听 VS Code 的 C/C++/Python 诊断错误，
 * 将原始诊断数据转发到 Airi 独立桌宠服务器 (standalone.py)。
 *
 * 不再内置 Python 后端、HTTP 服务器或桌面宠物进程。
 * 这些功能由 standalone.py + watcher.py 独立提供。
 *
 * 【数据流】
 *   VS Code 诊断变化 → handleDiagnosticsChanged
 *     → pushToStandalone(diagnosticsPayload)
 *       → POST http://127.0.0.1:19876/push
 *         → standalone 内建 generate_response → SSE → 桌宠气泡
 */

import * as vscode from 'vscode';
import * as http from 'http';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

// ============================================================================
// 模块级状态
// ============================================================================

/** Webview 面板的单例引用（备用显示） */
let panel: vscode.WebviewPanel | undefined;

/** 扩展自身路径（用于定位 desktop_pet/standalone.py） */
let extensionRoot = '';

/**
 * 支持监控的语言 ID 集合
 */
const supportedLanguageIds = new Set(['c', 'cpp', 'python']);

/** 独立桌宠服务器 (standalone.py) 端口（可用环境变量 AIRI_STANDALONE_PORT 覆盖） */
const STANDALONE_PORT = Number(process.env.AIRI_STANDALONE_PORT) || 19876;

/** 桌宠服务器健康状态缓存（避免每次诊断变化都探测） */
let standaloneHealthy = false;
let lastHealthCheckAt = 0;
const HEALTH_CHECK_INTERVAL_MS = 15000;

// ============================================================================
// activate()
// ============================================================================

export function activate(context: vscode.ExtensionContext) {
	console.log('[airi-monitor] activate() started');
	extensionRoot = context.extensionPath;

	// --- 注册命令 ---
	const openAssistantCommand = vscode.commands.registerCommand('vscode-anime-assistent.openAssistant', () => {
		createOrShowAssistantPanel(context);
	});
	const launchPetCommand = vscode.commands.registerCommand('vscode-anime-assistent.launchPet', () => {
		void launchStandalonePet();
	});
	context.subscriptions.push(openAssistantCommand, launchPetCommand);

	// --- 诊断监听 ---
	// 当语言服务器 / linter 检测到错误时触发（始终按工作区整体统计）
	const diagnosticsDisposable = vscode.languages.onDidChangeDiagnostics(() => {
		handleDiagnosticsChanged();
	});
	context.subscriptions.push(diagnosticsDisposable);

	// --- 启动时扫描已有诊断（有错误才处理，干净启动不打扰） ---
	// Webview 诊断面板改为按需打开：命令 "Open Anime Assistant"
	if (collectErrors().length > 0) { handleDiagnosticsChanged(); }

	vscode.window.showInformationMessage('Airi Monitor 已就绪 — 诊断消息将转发到桌宠');
	console.log(`[airi-monitor] Ready. Push target: http://127.0.0.1:${STANDALONE_PORT}/push`);
}

// ============================================================================
// deactivate()
// ============================================================================

export function deactivate() {
	panel = undefined;
}

// ============================================================================
// handleDiagnosticsChanged()
// ============================================================================

/** 上次推送到桌宠的内容签名（相同内容不重复推送） */
let lastPushSignature = '';

/** 工作区是否出现过受支持的错误（从未出过错时不推 all_clear） */
let hadRelevantErrors = false;

interface ErrorItem {
	file: string;
	languageId: string;
	message: string;
	source: string;
	line: number;
	character: number;
}

/** 从文件路径推断语言 ID（用于未在编辑器中打开、拿不到 languageId 的文件） */
function inferLanguageFromPath(fsPath: string): string {
	const ext = path.extname(fsPath).toLowerCase();
	if (ext === '.py' || ext === '.pyi') { return 'python'; }
	if (ext === '.c') { return 'c'; }
	if (['.cpp', '.cc', '.cxx', '.c++', '.h', '.hpp', '.hh', '.hxx'].includes(ext)) { return 'cpp'; }
	return '';
}

/** 收集诊断错误。传入 uris 时只收集这些文件，否则收集整个工作区。
 * 注意：语言服务器对已关闭文件的诊断仍保留在 getDiagnostics() 里，
 * 不能因为文档未打开就跳过，否则关掉报错文件会被误判为「全部修好」。 */
function collectErrors(uris?: readonly vscode.Uri[]): ErrorItem[] {
	const targets = uris ? new Set(uris.map((u) => u.toString())) : undefined;
	const items: ErrorItem[] = [];
	for (const [uri, diagnostics] of vscode.languages.getDiagnostics()) {
		if (targets && !targets.has(uri.toString())) { continue; }
		const document = vscode.workspace.textDocuments.find((doc) => doc.uri.toString() === uri.toString());
		const languageId = document?.languageId ?? inferLanguageFromPath(uri.fsPath);
		if (!languageId || !supportedLanguageIds.has(languageId)) { continue; }
		for (const d of diagnostics) {
			if (d.severity !== vscode.DiagnosticSeverity.Error) { continue; }
			items.push({
				file: uri.fsPath,
				languageId,
				message: d.message,
				source: d.source ?? 'unknown',
				line: d.range.start.line + 1,
				character: d.range.start.character + 1,
			});
		}
	}
	return items;
}

function handleDiagnosticsChanged(): void {
	// all_clear 必须按工作区整体判断：
	// 本次变化的文件没错误 ≠ 全部修好，其他文件（包括已关闭但语言服务器
	// 仍在跟踪的文件）可能还有错误
	const workspaceErrors = collectErrors();
	if (workspaceErrors.length > 0) { hadRelevantErrors = true; }

	// 发送到 Webview 备用面板（同样使用工作区整体口径，避免误导）
	postToWebview({
		type: 'diagnostics',
		payload: { count: workspaceErrors.length, items: workspaceErrors.slice(0, 20), timestamp: new Date().toISOString() }
	});

	// 推送到独立桌宠服务器（未运行时静默跳过，避免刷无意义请求）
	void (async () => {
		if (!(await checkStandaloneAlive())) { return; }
		// 从未出现过受支持的错误时不推 all_clear（例如无关语言的诊断事件）
		if (workspaceErrors.length === 0 && !hadRelevantErrors) { return; }

		const message = workspaceErrors.length > 0
			? {
				type: 'diagnostics' as const,
				payload: {
					count: workspaceErrors.length,
					items: workspaceErrors.slice(0, 20),
					timestamp: new Date().toISOString(),
					language: [...new Set(workspaceErrors.map((e) => e.languageId))].join(', '),
				},
			}
			: {
				trigger: 'all_clear' as const,
				error_count: 0,
				language: 'unknown',
				files: [],
				sample_errors: [],
			};

		// 相同错误状态不重复推送（避免每次诊断事件都让 Airi 重复吐槽）
		const signature = JSON.stringify([
			(message as { type?: string }).type ?? (message as { trigger?: string }).trigger,
			workspaceErrors.map((e) => `${e.file}|${e.line}|${e.message}`),
		]);
		if (signature === lastPushSignature) { return; }
		lastPushSignature = signature;

		pushToStandalone(message);
	})();
}

// ============================================================================
// checkStandaloneAlive() — 探测桌宠服务器是否在运行（带缓存）
// ============================================================================

async function checkStandaloneAlive(): Promise<boolean> {
	const now = Date.now();
	if (now - lastHealthCheckAt < HEALTH_CHECK_INTERVAL_MS) { return standaloneHealthy; }
	lastHealthCheckAt = now;
	standaloneHealthy = await new Promise<boolean>((resolve) => {
		const req = http.get(
			{ hostname: '127.0.0.1', port: STANDALONE_PORT, path: '/ping', timeout: 600 },
			(res) => { res.resume(); resolve(res.statusCode === 200); }
		);
		req.on('error', () => resolve(false));
		req.on('timeout', () => { req.destroy(); resolve(false); });
	});
	return standaloneHealthy;
}

// ============================================================================
// launchStandalonePet() — 从 VS Code 内一键启动桌宠 (standalone.py)
// ============================================================================

function findPythonPath(): string {
	const candidates = [
		process.env.AIRI_PYTHON_PATH,
		path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Python', 'Python312', 'python.exe'),
		'python',
	];
	for (const candidate of candidates) {
		if (!candidate) { continue; }
		if (candidate === 'python' || fs.existsSync(candidate)) { return candidate; }
	}
	return 'python';
}

async function launchStandalonePet(): Promise<void> {
	if (await checkStandaloneAlive()) {
		vscode.window.showInformationMessage('Airi 桌宠已经在运行了');
		return;
	}

	const petDir = path.join(extensionRoot, 'desktop_pet');
	const script = path.join(petDir, 'standalone.py');
	if (!fs.existsSync(script)) {
		vscode.window.showErrorMessage(`找不到桌宠脚本: ${script}`);
		return;
	}

	const python = findPythonPath();
	try {
		const child = spawn(python, [script], {
			cwd: petDir,
			detached: true,
			stdio: 'ignore',
			windowsHide: true,
		});
		child.on('error', (err) => {
			vscode.window.showErrorMessage(`启动桌宠失败: ${err.message}`);
		});
		child.unref();
	} catch (err) {
		vscode.window.showErrorMessage(`启动桌宠失败: ${err instanceof Error ? err.message : String(err)}`);
		return;
	}

	// 等服务器起来后确认
	const deadline = Date.now() + 8000;
	while (Date.now() < deadline) {
		await new Promise((resolve) => setTimeout(resolve, 500));
		lastHealthCheckAt = 0; // 强制重新探测
		if (await checkStandaloneAlive()) {
			lastHealthCheckAt = Date.now();
			vscode.window.showInformationMessage('Airi 桌宠已启动，开始监视你的代码 (￣▽￣)');
			return;
		}
	}
	vscode.window.showWarningMessage(
		`桌宠进程已启动但服务器未响应 (port ${STANDALONE_PORT})。` +
		'请检查 Python 环境是否安装了 pywebview (pip install pywebview)。'
	);
}

// ============================================================================
// pushToStandalone() — HTTP POST 到独立桌宠服务器
// ============================================================================

function pushToStandalone(message: unknown): void {
	try {
		const body = JSON.stringify(message);
		const req = http.request({
			hostname: '127.0.0.1', port: STANDALONE_PORT, path: '/push',
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Content-Length': Buffer.byteLength(body),
			},
			timeout: 500,
		}, () => { /* 忽略响应 */ });
		req.on('error', () => { /* 服务器未运行，静默跳过 */ });
		req.on('timeout', () => { req.destroy(); });
		req.write(body);
		req.end();
	} catch { /* 静默失败 */ }
}

// ============================================================================
// Webview 备用面板
// ============================================================================

function createOrShowAssistantPanel(context: vscode.ExtensionContext): void {
	if (panel) { panel.reveal(vscode.ViewColumn.Beside); return; }

	panel = vscode.window.createWebviewPanel(
		'airiAssistant', 'Airi Assistant', vscode.ViewColumn.Beside,
		{ enableScripts: true, retainContextWhenHidden: true }
	);
	panel.webview.html = getWebviewHtml();
	panel.onDidDispose(() => { panel = undefined; });
	context.subscriptions.push(panel);
}

function postToWebview(message: unknown): void {
	if (!panel) { return; }
	void panel.webview.postMessage(message);
}

function getWebviewHtml(): string {
	return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"/><style>
:root{color-scheme:dark;--bg:#1a1a2e;--text:#e8e8e8;--muted:#8888aa;--accent:#ff6b9d;--error-bg:rgba(255,71,87,0.12);--error-border:#ff4757}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);height:100vh;padding:12px;overflow-y:auto}
h2{color:var(--accent);margin-bottom:8px;font-size:14px}
.status{font-size:12px;color:var(--muted);margin-bottom:12px}
.error-item{background:var(--error-bg);border-left:3px solid var(--error-border);padding:6px 10px;margin:4px 0;border-radius:4px;font-size:12px}
.error-item .msg{color:var(--text)}
.error-item .meta{color:var(--muted);font-size:10px}
.empty{color:var(--muted);font-size:12px;text-align:center;padding:20px}
</style></head><body>
<h2>Airi Monitor</h2>
<p class="status">诊断消息自动转发至桌宠 (port 19876)</p>
<div id="list"><p class="empty">没有错误 — 一切正常</p></div>
<script>
const vscode=acquireVsCodeApi(),list=document.getElementById('list');
function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML}
window.addEventListener('message',function(e){
  var m=e.data;if(m.type!=='diagnostics')return;
  var items=m.payload.items||[],count=m.payload.count||0;
  if(count===0){list.innerHTML='<p class="empty">没有错误 — 一切正常</p>';return}
  var html='';items.slice(0,20).forEach(function(e){
    var fname=e.file.split(/[\\\\/]/).pop();
    html+='<div class="error-item"><div class="msg">'+esc(e.message)+'</div><div class="meta">'+esc(e.languageId)+' | '+esc(fname)+':'+esc(e.line)+'</div></div>';
  });list.innerHTML=html;
});
</script></body></html>`;
}
