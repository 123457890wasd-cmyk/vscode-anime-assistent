"""
============================================================================
main.py — Airi Monitor 的 Python 后端入口
============================================================================

【概述】
  Airi（愛莉）的大脑。通过 stdin/stdout 管道与 VS Code 扩展通信，
  接收诊断事件和心跳，生成傲娇风格的聊天回复。

【通信协议】
  输入（stdin）：每行一个 JSON 对象
    消息类型：
    - diagnostics：代码错误信息（含 count, items, timestamp）
    - heartbeat：编辑活动（editsInSession, file, languageId）
    - 自定义 context：{trigger, error_count, language, files, sample_errors}

  输出（stdout）：每行一个 JSON 对象
    消息类型：
    - chatMessage：Airi 普通聊天消息
    - errorAlert：Airi 错误提醒（特殊样式）
    - statusChange：系统状态变更

【运行方式】
  由 Node.js 扩展通过 child_process.spawn('python', ['main.py']) 启动。
"""

import sys
import json
import os

# 确保能导入同目录下的模块（Node.js spawn 的 CWD 可能不是此脚本所在目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 强制 stdout/stderr 使用 UTF-8 编码（Windows 默认 gbk 会导致乱码）
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from character import CHARACTER_NAME
from response_generator import generate_response, pick_corpus


def send_message(msg: dict):
    """将消息序列化为 JSON 并输出到 stdout，确保立即刷新"""
    print(json.dumps(msg, ensure_ascii=False))
    sys.stdout.flush()


def build_context(msg_type: str, payload: dict) -> dict:
    """从 Extension 发来的原始消息构建回复生成器需要的上下文"""
    context = {
        "trigger": msg_type,
        "error_count": 0,
        "language": "unknown",
        "files": [],
        "sample_errors": [],
    }

    if msg_type == "diagnostics":
        items = payload.get("items", [])
        context["error_count"] = payload.get("count", len(items))
        context["sample_errors"] = items[:5]  # 取前5个错误作为样本

        # 提取语言和文件信息
        languages = set()
        files = []
        for item in items:
            lang = item.get("languageId", "")
            if lang:
                languages.add(lang)
            f = item.get("file", "")
            if f and f not in files:
                files.append(f)

        context["language"] = ", ".join(languages) if languages else "unknown"
        context["files"] = files[:5]

    elif msg_type == "heartbeat":
        context["language"] = payload.get("languageId", "unknown")
        context["files"] = [payload.get("file", "")]
        context["error_count"] = payload.get("editsInSession", 0)

    return context


def main():
    """
    主循环：从 stdin 逐行读取 JSON，处理后返回傲娇回复。

    扩展设计：
    1. 收到 diagnostics → 生成 errorAlert 消息
    2. 收到 heartbeat → 不回复（太频繁）
    3. 收到自定义 context（含 trigger 字段）→ 按 trigger 类型生成对应回复
    """

    # 启动时发送 greeting
    greeting = pick_corpus("greeting")
    send_message({
        "type": "chatMessage",
        "payload": {"text": greeting}
    })

    # 标记已就绪
    send_message({
        "type": "statusChange",
        "payload": {"text": f"{CHARACTER_NAME} 已就绪"}
    })

    for line in sys.stdin:
        try:
            data = json.loads(line)

            msg_type = data.get("type", "")
            payload = data.get("payload", {})

            # 如果消息本身已经包含 trigger（来自扩展的自定义上下文）
            if "trigger" in data:
                response = generate_response(data)
            elif "trigger" in payload:
                response = generate_response(payload)
            else:
                # 从原始事件构建上下文再生成回复
                context = build_context(msg_type, payload)

                # 心跳事件不回复
                if msg_type == "heartbeat":
                    continue

                response = generate_response(context)

            if response:
                send_message(response)

        except json.JSONDecodeError:
            # 静默跳过无效 JSON 行
            pass
        except Exception:
            # 其他异常不中断循环
            pass


if __name__ == "__main__":
    main()
