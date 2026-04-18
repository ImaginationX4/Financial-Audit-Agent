# -*- coding: utf-8 -*-
"""
Agent 2: 提取器
职责：从图片中提取结构化财务数据，并在验证失败时自动重试
输入：base64 图片 + 文档类型 + 可选的上次错误上下文
输出：对应的 Pydantic Schema 实例

核心设计：
    - 错误恢复（Error Recovery）：ValidationError / RuntimeError 均可重试
    - 错误上下文注入：将上次失败原因注入 prompt，引导 LLM 修正
    - 路由表（Route Map）：doc_type → (Schema, base_prompt) 的映射
"""
import json
from pydantic import BaseModel, ValidationError

from infra.llm_client import call_doubao_vision
from infra.json_parser import robust_json_extract
from schemas.voucher import AccountingVoucherSchema
from schemas.invoice import InvoiceSchema
from schemas.bank import BankReceiptSchema
from agents.config import CORP_API_KEY, CORP_VISION_EP


# --- 路由表：文档类型 → (目标 Schema, 基础 Prompt) ---
_ROUTE_MAP: dict[str, tuple[type[BaseModel], str]] = {
    "转账凭证": (
        AccountingVoucherSchema,
        (
            "你是一个专业的财务核算专家。请精准提取这张【转账凭证】图片中的核心数据。\n"
            "只提取借方相关的明细行，忽略贷方。\n"
        ),
    ),
    "发票": (
        InvoiceSchema,
        (
            "你是一个专业的税务发票识别专家。请精准提取这张【发票】图片中的核心信息。\n"
            "重点关注：发票名称、发票代码、发票号码、销售方名称、不含税金额、税率、含税金额。\n"
            
        ),
    ),
    "银行转账单": (
        BankReceiptSchema,
        (
            "你是一个专业的银行流水识别专家。请精准提取这张【银行转账单/回单】图片中的交易金额。\n"
            "重点关注：支付方式、银行流水号、收款人名称、实际扣款金额。\n"
        ),
    ),
}


def _build_prompt(base_prompt: str, schema_json: str, error_context: str | None) -> str:
    """
    组装最终的提取 prompt。

    契约：
        error_context 存在时，将其注入为【重要修正要求】段落
        schema_json 作为强约束附在末尾
    """
    error_instruction = ""
    if error_context:
        error_instruction = (
            f"\n\n⚠️ 【重要修正要求】\n"
            f"你上一次的提取结果有以下错误，请仔细阅读并在本次修正：\n"
            f"{error_context}\n"
            f"请特别注意这些字段，确保本次输出符合要求。\n"
        )

    return (
        f"{base_prompt}"
        f"{error_instruction}"
        "你必须且只能以 JSON 格式返回数据，不要包含任何自然语言解释。\n"
        "请严格遵守以下 JSON Schema：\n"
        f"{schema_json}"
    )


def extract_once(
    base64_img: str,
    doc_type: str,
    error_context: str | None = None,
) -> BaseModel:
    """
    单次提取：调用 LLM → 解析 JSON → Pydantic 验证。

    契约：
        输入：合法 base64 图片、已知文档类型
        输出：对应 Schema 的 Pydantic 实例
    异常：
        KeyError：doc_type 不在路由表中
        RuntimeError：LLM 返回非法 JSON
        ValidationError：JSON 合法但不满足 Schema 业务规则
    """
    if doc_type not in _ROUTE_MAP:
        raise KeyError(f"未知文档类型: {doc_type}，合法值为 {list(_ROUTE_MAP.keys())}")

    target_schema, base_prompt = _ROUTE_MAP[doc_type]
    schema_json = json.dumps(
        target_schema.model_json_schema(), ensure_ascii=False, indent=2
    )
    prompt = _build_prompt(base_prompt, schema_json, error_context)

    raw_response = call_doubao_vision(
        base64_img=base64_img,
        api_key=CORP_API_KEY,
        model_endpoint=CORP_VISION_EP,
        prompt=prompt,
    )

    extracted_dict = robust_json_extract(raw_response)
    if not extracted_dict:
        raise RuntimeError("大模型未能返回有效 JSON")

    # ValidationError 不在此处捕获，交由 extract_with_retry 处理
    return target_schema(**extracted_dict)


def extract_with_retry(
    base64_img: str,
    doc_type: str,
    max_retries: int = 2,
) -> BaseModel:
    """
    带重试的提取器：错误恢复的核心实现。

    错误恢复策略：
        ValidationError → 将 Pydantic 错误详情注入下次 prompt（引导 LLM 修正）
        RuntimeError    → 将 JSON 解析错误注入下次 prompt
        其他异常        → 不重试，直接抛出（未知错误不应掩盖）

    契约：
        输入：合法 base64 图片、已知文档类型
        输出：对应 Schema 的 Pydantic 实例
    异常：
        RuntimeError：超出最大重试次数后仍失败
    """
    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            return extract_once(base64_img, doc_type, error_context=last_error)

        except ValidationError as e:
            last_error = f"上次提取的数据验证失败，具体错误：{e.errors()}"
            print(f"[重试 {attempt + 1}/{max_retries}] ValidationError: {last_error}")

        except RuntimeError as e:
            last_error = f"上次返回的不是合法 JSON，具体错误：{str(e)}"
            print(f"[重试 {attempt + 1}/{max_retries}] RuntimeError: {last_error}")

        except Exception:
            raise  # 未知异常：不重试，让调用方感知

    raise RuntimeError(
        f"经 {max_retries} 次重试后仍失败\n最后错误: {last_error}"
    )