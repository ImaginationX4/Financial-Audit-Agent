# -*- coding: utf-8 -*-
"""
infra/json_parser.py
职责：将 LLM 返回的原始文本可靠地解析为 Python 对象
输入：LLM 原始文本字符串（可能含 markdown fence、trailing comma、非法转义等噪声）
输出：Python dict / list，或 None（解析失败时）

核心设计（三阶段管道）：
    1. 信号隔离（Isolation）   - 从噪声文本中定位 JSON 边界
    2. 缺陷净化（Sanitization）- 修复 LLM 常见的 JSON 语法缺陷
    3. 反序列化（Deserialization）- 标准 json.loads + 精准错误报告


"""
import re
import json
from typing import Any
import logging


def _isolate_json(text: str) -> str:
    # 策略 1：markdown fence（不动）
    markdown_pattern = r"```(?:json)?\s*(.*?)\s*```"
    match = re.search(markdown_pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 策略 2：起点决定类型，终点严格匹配
    first_brace   = text.find("{")
    first_bracket = text.find("[")

    starts = [pos for pos in (first_brace, first_bracket) if pos != -1]
    if not starts:
        return text  # 兜底

    start_idx = min(starts)
    closer = "}" if text[start_idx] == "{" else "]"
    end_idx = text.rfind(closer)

    if end_idx > start_idx:
        return text[start_idx : end_idx + 1]

    return text  # 兜底


def _sanitize_json(json_str: str) -> str:
    """
    阶段 2：修复 LLM 常见的 JSON 语法缺陷。

    修复项：
        1. Trailing comma：字典或列表末尾多余的逗号（LLM 极高频错误）
           例：{"key": "value",} → {"key": "value"}
        2. 非法单反斜杠：财务科目等场景中出现的 \ 字符
           例：科目\明细 → 科目\\明细
           注意：保留合法转义（\n \t \r \" \\ \/ \b \f \\uXXXX）
    """
    # 修复 1：trailing comma
    json_str = re.sub(r",\s*([\]}])", r"\1", json_str)

    # 修复 2：非法单反斜杠（负向前瞻 + 负向后瞻）
    json_str = re.sub(r'(?<!\\)\\(?![nrt"\\/bfu])', r"\\\\", json_str)

    return json_str


def robust_json_extract(text: str) -> Any:
    """
    主入口：三阶段 JSON 解析管道。

    契约：
        输入：任意 LLM 原始输出字符串
        输出：Python 对象（dict / list / str / int），或 None（无法解析时）

    这是系统对外暴露的唯一接口，调用方只需关心返回值是否为 None。
    None 表示解析彻底失败，调用方应将其视为 RuntimeError 的触发条件。
    """
    text = text.strip()

    json_str = _isolate_json(text)
    json_str = _sanitize_json(json_str)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # 预先拼接好错误信息字符串
        error_msg = (
            "\n" + "=" * 60 + "\n"
            "🚨 [JSON 解析失败] 三阶段管道无法处理以下内容\n"
            f"   错误位置：{e}\n"
            "   清洗后的 JSON 文本：\n"
            f"{json_str}\n"
            + "=" * 60 + "\n"
        )
        # 通过 logging 模块发射警告级别的日志，这样 caplog 就能精准捕获到 "JSON解析失败" 了
        logging.warning(error_msg)
        return None