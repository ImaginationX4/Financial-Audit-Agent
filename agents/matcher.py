# -*- coding: utf-8 -*-
"""
Agent 3: 匹配器
职责：将同一业务组的凭证、发票、银行回单送入 LLM 进行对账，输出差额和推理
输入：三类 Pydantic Schema 实例的列表
输出：MatchedTransactionGroupSchema 实例

核心设计：
    - 纯文本 LLM 调用（不需要视觉模型）
    - Schema 驱动输出：将目标 Schema 注入 prompt 作为强约束
    - 单次调用，不重试（对账结果需要人工复核，重试无意义）
"""
import json
from typing import List

from infra.llm_client import call_doubao_text
from infra.json_parser import robust_json_extract
from schemas.voucher import AccountingVoucherSchema
from schemas.invoice import InvoiceSchema
from schemas.bank import BankReceiptSchema
from schemas.match import MatchedTransactionGroupSchema
from agents.config import CORP_API_KEY, CORP_TEXT_EP


def match_financial_documents(
    vouchers: List[AccountingVoucherSchema],
    invoices: List[InvoiceSchema],
    receipts: List[BankReceiptSchema],
) -> MatchedTransactionGroupSchema:
    """
    将同一业务组的三类单据送入 LLM 进行对账。

    契约：
        输入：同一业务组内的凭证列表、发票列表、银行回单列表（可以为空列表）
        输出：MatchedTransactionGroupSchema 实例，包含差额和 AI 推理说明
    异常：
        RuntimeError：LLM 未返回合法 JSON，或返回的不是单个对象
        ValidationError：JSON 合法但不满足 MatchedTransactionGroupSchema 规则

    注意：
        此函数不做重试。对账结果涉及业务判断，错误应暴露给调用方处理。
    """
    # --- 组装输入数据 ---
    input_data = {
        "当前关联的转账凭证": [v.model_dump(mode="json") for v in vouchers],
        "当前关联的发票": [i.model_dump(mode="json") for i in invoices],
        "当前关联的银行流水": [r.model_dump(mode="json") for r in receipts],
    }
    input_json_str = json.dumps(input_data, ensure_ascii=False, indent=2)
    schema_definition = json.dumps(
        MatchedTransactionGroupSchema.model_json_schema(), ensure_ascii=False, indent=2
    )

    prompt = (
        "你是一个顶级的财务审计专家。我将为你提供同一家公司的一组财务单据，"
        "包括转账凭证、发票和银行流水。\n"
        "你的任务是对这组数据进行内部核算和对账。\n\n"
        "核算原则：\n"
        "- 计算凭证账面的总金额、发票含税总金额、银行流水的实际支付总金额。\n"
        "- 算出差额（invoice_difference = 发票含税总额 - 凭证账面总额）。\n"
        "- 在 match_reasoning 中详细说明这笔业务的合理性，"
        "以及是否存在少开发票、多付款等情况。\n\n"
        f"【输入数据】\n{input_json_str}\n\n"
        "【输出要求】\n"
        "你必须且只能以单个 JSON 对象（Object）的格式返回数据，不要包裹在数组中。\n"
        "绝对不要输出任何自然语言解释。\n"
        "请严格遵守以下 JSON Schema 的结构生成该对象：\n"
        f"{schema_definition}"
    )

    raw_response = call_doubao_text(
        api_key=CORP_API_KEY,
        model_endpoint=CORP_TEXT_EP,
        prompt=prompt,
    )

    extracted_data = robust_json_extract(raw_response)

    if not extracted_data or not isinstance(extracted_data, dict):
        raise RuntimeError(
            f"大模型未能返回有效的单个 JSON 对象。\n原始响应：{raw_response}"
        )

    return MatchedTransactionGroupSchema(**extracted_data)