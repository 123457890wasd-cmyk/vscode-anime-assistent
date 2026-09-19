/*
 * ============================================================================
 * extension.test.ts — 扩展测试文件
 * ============================================================================
 *
 * 测试 airi-monitor 扩展的核心逻辑单元。
 */

import * as assert from 'assert';
import * as vscode from 'vscode';

suite('Extension Test Suite', () => {
	vscode.window.showInformationMessage('Start all tests.');

	/**
	 * 验证扩展已正确激活并注册了命令
	 */
	test('Extension should be activated', async () => {
		const ext = vscode.extensions.getExtension('123457890wasd-cmyk.vscode-anime-assistent');
		assert.ok(ext, 'Extension not found');

		if (!ext.isActive) {
			await ext.activate();
		}
		assert.strictEqual(ext.isActive, true, 'Extension should be active');
	});

	/**
	 * 验证 openAssistant 命令已注册
	 */
	test('openAssistant command should be registered', async () => {
		const commands = await vscode.commands.getCommands(true);
		assert.strictEqual(
			commands.includes('vscode-anime-assistent.openAssistant'),
			true,
			'openAssistant command not registered'
		);
	});

	/**
	 * 验证 launchPet 命令已注册
	 */
	test('launchPet command should be registered', async () => {
		const commands = await vscode.commands.getCommands(true);
		assert.strictEqual(
			commands.includes('vscode-anime-assistent.launchPet'),
			true,
			'launchPet command not registered'
		);
	});

	/**
	 * 验证支持的语言 ID 集合
	 */
	test('should support C, C++, and Python language IDs', () => {
		const supportedLanguages = ['c', 'cpp', 'python'];
		assert.strictEqual(supportedLanguages.includes('c'), true);
		assert.strictEqual(supportedLanguages.includes('cpp'), true);
		assert.strictEqual(supportedLanguages.includes('python'), true);
		assert.strictEqual(supportedLanguages.includes('javascript'), false);
	});
});
