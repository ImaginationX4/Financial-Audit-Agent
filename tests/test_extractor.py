# -*- coding: utf-8 -*-
"""
test_extractor.py — extract_with_retry 行为测试

三条命题各自证明一个 except 分支：
    命题 1 — ValidationError 触发重试，且错误上下文被注入下次调用
    命题 2 — 超出 max_retries 后抛 RuntimeError，调用次数恰好 max_retries + 1
    命题 3 — 未知异常立即透传，不重试（call_count == 1）

mock 策略：
    patch("extractor.extract_once")，只隔离 LLM 这条外部依赖
    被测函数 extract_with_retry 的控制流本身不 mock，完整执行
"""
import pytest
from unittest.mock import patch, call, MagicMock
from pydantic import BaseModel, ValidationError

from agents.extractor import extract_with_retry


# ─────────────────────────────────────────────
# 辅助：构造一个真实的 ValidationError
# ValidationError 不能直接实例化，必须从失败的 Pydantic 校验中捕获
# ─────────────────────────────────────────────
def _make_validation_error() -> ValidationError:
    class _Strict(BaseModel):
        value: int  # 传 str 必然触发 ValidationError

    with pytest.raises(ValidationError) as exc_info:
        _Strict(value="not-an-int")
    return exc_info.value


# ─────────────────────────────────────────────
# 常量：测试用的假输入，内容不重要，只要类型正确
# ─────────────────────────────────────────────
FAKE_IMG = "base64encodedstring"
DOC_TYPE = "发票"


# ══════════════════════════════════════════════
# 命题 1：ValidationError → 重试 + error_context 注入
# ══════════════════════════════════════════════

def test_extract_with_retry_injects_error_context_on_validation_error():
    """
    第一次调用抛 ValidationError，第二次成功。
    断言：
        - 总调用次数 == 2（重试了一次）
        - 第二次调用时 error_context 不为 None（错误被注入）
        - 返回值是第二次的成功结果
    """
    validation_error = _make_validation_error()
    success_result = MagicMock(spec=BaseModel)

    # side_effect 是一个序列：第一次抛异常，第二次返回正常值
    with patch("agents.extractor.extract_once", side_effect=[validation_error, success_result]) as mock_once:
        result = extract_with_retry(FAKE_IMG, DOC_TYPE, max_retries=2)

    # 总调用次数
    assert mock_once.call_count == 2

    # 第一次调用时 error_context 为 None（首次，没有上次错误）
    first_call_kwargs = mock_once.call_args_list[0].kwargs
    assert first_call_kwargs["error_context"] is None

    # 第二次调用时 error_context 已被填入（含上次失败信息）
    second_call_kwargs = mock_once.call_args_list[1].kwargs
    assert second_call_kwargs["error_context"] is not None
    assert "验证失败" in second_call_kwargs["error_context"]

    # 最终返回的是成功结果
    assert result is success_result


# ══════════════════════════════════════════════
# 命题 2：超出 max_retries → RuntimeError，调用次数恰好 max_retries + 1
# ══════════════════════════════════════════════

def test_extract_with_retry_raises_after_max_retries_exhausted():
    """
    每次调用都抛 RuntimeError，共重试 max_retries 次。
    断言：
        - 最终抛出 RuntimeError（含"重试"语义）
        - extract_once 被调用恰好 max_retries + 1 次（首次 + 重试次数）
    """
    max_retries = 2

    with patch("agents.extractor.extract_once", side_effect=RuntimeError("JSON 解析失败")) as mock_once:
        with pytest.raises(RuntimeError) as exc_info:
            extract_with_retry(FAKE_IMG, DOC_TYPE, max_retries=max_retries)

    # 调用次数精确：不能多（浪费），不能少（没有完整重试）
    assert mock_once.call_count == max_retries + 1

    # 最终异常含业务语义，不是原始异常透传
    assert "重试" in str(exc_info.value)


# ══════════════════════════════════════════════
# 命题 3：未知异常 → 立即透传，不重试
# ══════════════════════════════════════════════

def test_extract_with_retry_reraises_unknown_exception_without_retry():
    """
    extract_once 抛出 KeyError（不在 except 捕获范围内）。
    断言：
        - KeyError 被原样抛出（类型不变，不被包装）
        - extract_once 只被调用 1 次（未触发任何重试）
    """
    with patch("agents.extractor.extract_once", side_effect=KeyError("未知文档类型")) as mock_once:
        with pytest.raises(KeyError):
            extract_with_retry(FAKE_IMG, DOC_TYPE, max_retries=2)

    # call_count == 1 是这条命题的核心断言
    # 如果是 2 或 3，说明 except Exception: raise 没有正确阻断重试循环
    assert mock_once.call_count == 1