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

    # ── 1. 提取金额 ──────────────────────────────────
    voucher_credit = sum(
        item.credit_amount or 0.0
        for v in vouchers
        for item in v.line_items
        if item.subject_name and "银行存款" in item.subject_name
    )
    bank_total = sum(r.actual_paid_amount or 0.0 for r in receipts)
    invoice_total = sum(i.total_amount_with_tax or 0.0 for i in invoices)

    voucher_bank_diff = round(voucher_credit - bank_total, 2)
    invoice_bank_diff = round(invoice_total - bank_total, 2)

    # ── 2. 规则引擎 → flags ──────────────────────────
    flags = []

    flags = []

    if voucher_credit == 0 and bank_total > 0:
        flags.append("账外支付")
    if bank_total == 0 and voucher_credit > 0:
        flags.append("虚构凭证")
    if not invoices:
        flags.append("无票支出")
    if voucher_bank_diff != 0:
        flags.append("金额篡改")
    if invoice_bank_diff != 0:
        flags.append("发票金额不符")
    
    if not flags:
        flags.append("通过")

    # ── 3. LLM 生成审计建议 ──────────────────────────
    prompt = f"""你是一名专业审计师，请根据以下审计结果给出简洁的审计建议。

异常标记：{flags}
凭证贷方合计：{voucher_credit}
银行实付合计：{bank_total}
发票含税合计：{invoice_total}
凭证与银行差异：{voucher_bank_diff}
发票与银行差异：{invoice_bank_diff}

要求：
- 针对每个异常标记给出具体核查建议
- 若为"通过"则给出简短确认意见
- 不超过150字
- 直接输出建议文字，不要JSON包装
"""
    audit_suggestion = call_doubao_text(
        api_key=CORP_API_KEY,
        model_endpoint=CORP_TEXT_EP,
        prompt=prompt,
    )

    # ── 4. 组装输出 ───────────────────────────────────
    return MatchedTransactionGroupSchema(
        matched_vouchers=vouchers,
        matched_invoices=invoices,
        matched_bank_receipts=receipts,
        voucher_bank_diff=voucher_bank_diff,
        invoice_bank_diff=invoice_bank_diff,
        flags=flags,
        audit_suggestion=audit_suggestion,
    )
