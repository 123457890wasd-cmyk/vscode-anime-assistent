"""
============================================================================
response_generator.py — 回复生成器
============================================================================

负责根据上下文生成 Airi 的傲娇台词。

生成策略：
1. 如果设置了环境变量 DEEPSEEK_API_KEY，调用 DeepSeek API
2. 否则从本地语料库随机抽取匹配场景的台词

上下文格式（由 Extension 传入）：
{
    "trigger": "diagnostics" | "all_clear" | "greeting" | "heartbeat",
    "error_count": int,
    "language": "python" | "c" | "cpp",
    "files": ["file1.py", "file2.py"],
    "sample_errors": [
        {"file": "...", "line": 23, "message": "...", "source": "..."},
        ...
    ]
}
"""

import os
import random
import json
from corpus import (
    SYNTAX_ERROR, TYPE_ERROR, IMPORT_ERROR, NAME_ERROR,
    MANY_ERRORS, ALL_CLEAR, GREETING, IDLE, ENCOURAGE,
)


def classify_error_category(message: str, source: str = "") -> str:
    """根据错误消息文本推断错误类别"""
    msg_lower = message.lower()

    # 语法错误关键词
    syntax_keywords = ["syntax", "invalid syntax", "expected", "unexpected",
                       "missing", "eof", "indentation", "indent", "token"]
    if any(kw in msg_lower for kw in syntax_keywords):
        return "syntax_error"

    # 类型错误关键词
    type_keywords = ["type", "cannot be", "not assignable", "has no attribute",
                     "is not", "incompatible", "cast", "conversion"]
    if any(kw in msg_lower for kw in type_keywords):
        return "type_error"

    # 导入错误关键词
    import_keywords = ["module", "import", "no module named", "cannot find",
                       "unresolved", "could not find", "not found"]
    if any(kw in msg_lower for kw in import_keywords):
        return "import_error"

    # 名称错误关键词
    name_keywords = ["is not defined", "undefined", "unresolved reference",
                     "cannot find name", "undeclared", "nameerror"]
    if any(kw in msg_lower for kw in name_keywords):
        return "name_error"

    return "syntax_error"  # 默认归类为语法错误


def categorize_errors(errors: list) -> str:
    """根据错误列表推断主要错误类别"""
    if not errors:
        return "syntax_error"

    # 统计各类别出现次数
    counts = {}
    for err in errors:
        cat = classify_error_category(err.get("message", ""), err.get("source", ""))
        counts[cat] = counts.get(cat, 0) + 1

    # 返回最常见的类别
    return max(counts, key=counts.get)


def pick_corpus(category: str, variables: dict = None) -> str:
    """从语料库中随机选取一条台词，并替换变量占位符"""
    corpus_map = {
        "syntax_error": SYNTAX_ERROR,
        "type_error": TYPE_ERROR,
        "import_error": IMPORT_ERROR,
        "name_error": NAME_ERROR,
        "many_errors": MANY_ERRORS,
        "all_clear": ALL_CLEAR,
        "greeting": GREETING,
        "idle": IDLE,
        "encourage": ENCOURAGE,
    }

    lines = corpus_map.get(category, ENCOURAGE)
    text = random.choice(lines)

    # 替换变量占位符（如 {count}, {line}, {language}）
    if variables:
        for key, value in variables.items():
            text = text.replace("{" + key + "}", str(value))

    return text


def try_deepseek_api(context: dict) -> str | None:
    """尝试调用 DeepSeek API 生成回复。失败返回 None。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None

    try:
        # 延迟导入，避免在无 openai 库时崩溃
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )

        from character import SYSTEM_PROMPT

        trigger = context.get("trigger", "diagnostics")
        error_count = context.get("error_count", 0)
        language = context.get("language", "unknown")
        files = context.get("files", [])

        sample_text = ""
        sample_errors = context.get("sample_errors", [])
        if sample_errors:
            for e in sample_errors[:3]:
                sample_text += f"- {e.get('file', '')}:{e.get('line', '?')} → {e.get('message', '')}\n"

        user_prompt = f"[触发事件: {trigger}] 当前错误数: {error_count}, 语言: {language}, 涉及文件: {', '.join(files[:3])}"

        if sample_text:
            user_prompt += f"\n错误示例:\n{sample_text}"

        if trigger == "greeting":
            user_prompt += "\n请用傲娇的方式打招呼。"
        elif trigger == "all_clear":
            user_prompt += "\n程序员刚才把错误全修好了，请用傲娇的方式表示一下。"
        elif trigger == "diagnostics":
            user_prompt += "\n程序员刚写了一堆错误，请用傲娇的方式吐槽并悄悄给出建议。"

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=150,
            temperature=0.9,
        )

        content = response.choices[0].message.content
        if content and content.strip():
            return content.strip()

    except Exception:
        pass

    return None


def generate_response(context: dict) -> dict:
    """
    主入口：根据上下文生成回复消息。

    返回格式：
    {
        "type": "chatMessage" | "errorAlert",
        "payload": {"text": "回复文本"}
    }
    """
    trigger = context.get("trigger", "diagnostics")
    error_count = context.get("error_count", 0)
    language = context.get("language", "python")
    errors = context.get("sample_errors", [])

    # 确定消息类型
    if trigger == "diagnostics" and error_count > 0:
        msg_type = "errorAlert"
    else:
        msg_type = "chatMessage"

    # 尝试 DeepSeek API
    api_result = try_deepseek_api(context)
    if api_result:
        return {"type": msg_type, "payload": {"text": api_result}}

    # Fallback: 从语料库抽取
    if trigger == "greeting":
        text = pick_corpus("greeting")
    elif trigger == "all_clear":
        text = pick_corpus("all_clear")
    elif trigger == "diagnostics":
        if error_count >= 5:
            text = pick_corpus("many_errors", {"count": error_count, "language": language})
        else:
            category = categorize_errors(errors)
            variables = {"language": language}
            if errors:
                variables["line"] = errors[0].get("line", "?")
            text = pick_corpus(category, variables)
    elif trigger == "heartbeat":
        # 心跳事件不生成回复（太频繁）
        return None
    else:
        text = pick_corpus("encourage")

    return {"type": msg_type, "payload": {"text": text}}
