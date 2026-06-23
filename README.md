# Airi Monitor (vscode-anime-assistent)

一个 VS Code 扩展，在编辑器侧边栏中陪伴你写代码的二次元傲娇助手 **Airi（愛莉）**。

## 功能

- **实时 Bug 检测** — 监控 C/C++/Python 文件的编译错误，Airi 会用傲娇的语气提醒你
- **聊天式交互** — 对话气泡界面，像 QQ 聊天一样，不是冷冰冰的 CLI 输出
- **傲娇人格** — 嘴上吐槽你代码写得烂，但其实很关心你有没有修好 bug
- **智能回复** — 支持 DeepSeek API（可选）或本地语料库

## 使用方式

1. 安装扩展后，VSCode 启动时自动激活
2. 在侧边栏打开 "Airi Assistant" 面板（`Ctrl+Shift+P` → `Open Anime Assistant`）
3. 写代码时如果出现编译错误，Airi 会自动弹出聊天消息提醒你

## 配置

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（可选） | 未设置 → 使用本地语料库 |

## 开发

```bash
npm install
npm run compile    # 编译 TypeScript
npm run watch      # 监听模式
npm test           # 运行测试
```

## 项目结构

```
├── src/extension.ts          # VS Code 扩展入口
├── python_backend/           # Python 后端
│   ├── main.py               # 消息路由入口
│   ├── character.py          # 角色人格定义
│   ├── corpus.py             # 本地傲娇语料库
│   ├── response_generator.py # 回复生成器
│   └── requirements.txt      # Python 依赖
└── out/                      # 编译产物
```

## License

MIT
