# -*- coding: utf-8 -*-
"""
tests/test_json_parser.py

测试对象：infra/json_parser.py
测试策略：纯函数，无副作用，无需 mock
覆盖层次：
    - _isolate_json   : 信号隔离阶段
    - _sanitize_json  : 缺陷净化阶段
    - robust_json_extract : 主入口（含失败路径）
"""
import pytest
from infra.json_parser import _isolate_json, _sanitize_json, robust_json_extract


# ==========================================
# 命题 1：根据输入格式自动提取 JSON 边界
# ==========================================

class TestIsolateJson:

    def test_extracts_json_from_markdown_fence(self):
        # LLM 最常见的输出格式：```json ... ```
        text = '```json\n{"key": "value"}\n```'
        result = _isolate_json(text)
        assert result == '{"key": "value"}'

    def test_extracts_json_from_plain_markdown_fence(self):
        # 有时 LLM 只写 ``` 不写 json
        text = '```\n{"key": "value"}\n```'
        result = _isolate_json(text)
        assert result == '{"key": "value"}'

    def test_extracts_json_object_from_surrounding_text(self):
        # LLM 在 JSON 前后加了自然语言
        text = '好的，以下是提取结果：{"key": "value"} 请确认。'
        result = _isolate_json(text)
        assert result == '{"key": "value"}'

    def test_extracts_json_array_from_surrounding_text(self):
        # 列表形式的 JSON
        text = '结果如下：[{"key": "value"}]'
        result = _isolate_json(text)
        assert result == '[{"key": "value"}]'

    def test_extracts_outermost_brackets_when_nested(self):
        # 嵌套结构：必须取最外层，不能截断
        text = '{"outer": {"inner": "value"}}'
        result = _isolate_json(text)
        assert result == '{"outer": {"inner": "value"}}'

    def test_prefers_markdown_fence_over_bracket_search(self):
        # markdown fence 优先级高于括号搜索
        text = '有一些文字 {"fake": true} ```json\n{"real": true}\n```'
        result = _isolate_json(text)
        assert result == '{"real": true}'

    def test_array_before_object_takes_array(self):
        # 起点是 [，终点只找 ]，不碰 }
        text = '[1, 2, 3] 无关文字 {"key": "value"}'
        result = _isolate_json(text)
        assert result == '[1, 2, 3]'

    def test_object_before_array_takes_object(self):
        # 起点是 {，终点只找 }，不碰 ]
        text = '{"key": "value"} 无关文字 [1, 2, 3]'
        result = _isolate_json(text)
        assert result == '{"key": "value"}'

    # 缺失case 1：混合括号结构
    def test_mixed_bracket_types_in_text(self):
        text = '[1,2,3] 无关文字 {"key": "value"}'
        result = _isolate_json(text)
        # 应该取哪个？当前实现会返回非法JSON
    
    # 缺失case 2：空JSON
    def test_empty_object(self):
        result = robust_json_extract('{}')
        assert result == {}
    
    # 缺失case 3：多层嵌套trailing comma
    def test_nested_trailing_comma(self):
        result = robust_json_extract('{"a": {"b": 1,},}')
        assert result == {"a": {"b": 1}}
# ==========================================
# 命题 2：修复 JSON 里的非法语法缺陷
# ==========================================

class TestSanitizeJson:

    def test_removes_trailing_comma_in_object(self):
        # 字典末尾多余逗号
        json_str = '{"key": "value",}'
        result = _sanitize_json(json_str)
        assert result == '{"key": "value"}'

    def test_removes_trailing_comma_in_array(self):
        # 列表末尾多余逗号
        json_str = '["a", "b", "c",]'
        result = _sanitize_json(json_str)
        assert result == '["a", "b", "c"]'

    def test_removes_trailing_comma_with_whitespace(self):
        # 逗号后有空白字符的情况
        json_str = '{"key": "value",  \n}'
        result = _sanitize_json(json_str)
        assert result == '{"key": "value"}'

    def test_fixes_illegal_single_backslash(self):
        # 财务科目中出现的非法单反斜杠，如"开发成本\一期"
        json_str = '{"subject": "开发成本\\一期"}'
        result = _sanitize_json(json_str)
        # 非法 \ 应被转义为 \\，使 JSON 合法
        import json
        parsed = json.loads(result)
        assert "一期" in parsed["subject"]

    def test_preserves_legal_escape_sequences(self):
        # 合法转义不能被破坏：\n \t \r \" \\
        json_str = '{"note": "line1\\nline2\\ttab"}'
        result = _sanitize_json(json_str)
        assert result == '{"note": "line1\\nline2\\ttab"}'

    def test_preserves_unicode_escape(self):
        # \uXXXX 是合法的 Unicode 转义，不能被破坏
        json_str = '{"char": "\\u4e2d\\u6587"}'
        result = _sanitize_json(json_str)
        assert result == '{"char": "\\u4e2d\\u6587"}'


# ==========================================
# 命题 3：主入口失败时精准报告问题，不静默
# ==========================================

class TestRobustJsonExtract:

    def test_returns_dict_on_valid_json(self):
        result = robust_json_extract('{"key": "value"}')
        assert result == {"key": "value"}

    def test_returns_list_on_valid_json_array(self):
        result = robust_json_extract('[{"key": "value"}]')
        assert isinstance(result, list)
        assert result[0] == {"key": "value"}

    def test_handles_markdown_fence_end_to_end(self):
        # 完整管道：markdown → 隔离 → 净化 → 反序列化
        text = '```json\n{"tax_rate": "9%",}\n```'
        result = robust_json_extract(text)
        assert result == {"tax_rate": "9%"}

    def test_returns_none_on_completely_invalid_input(self):
        # 无法解析时返回 None，不抛异常（调用方负责处理 None）
        result = robust_json_extract("这完全不是 JSON")
        assert result is None

    def test_returns_none_on_empty_string(self):
        result = robust_json_extract("")
        assert result is None

    # 替换原来的 test_prints_error_detail_on_failure
    def test_logs_warning_on_failure(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            robust_json_extract("不是JSON的内容")
        assert "JSON 解析失败" in caplog.text

    def test_handles_trailing_comma_end_to_end(self):
        # 净化阶段修复后能被成功反序列化
        result = robust_json_extract('{"key": "value",}')
        assert result == {"key": "value"}

    def test_handles_nested_structure(self):
        # 嵌套 JSON 不被截断
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = robust_json_extract(text)
        assert result["outer"]["inner"] == [1, 2, 3]
    