# -*- coding: utf-8 -*-
"""
schemas/match.py — 对账结果组


"""
from pydantic import BaseModel, Field


class MatchedTransactionGroupSchema(BaseModel):
    group_id: str = Field(
        description=(
            "业务组核心标识：提取一个简短、高业务可读性的名称，"
            "例如'XX公司-桩基工程款'，作为本组单据的唯一标签"
        )
    )
    group_total_book_amount: float = Field(
        description="组内凭证账面金额合计，单位元"
    )
    group_total_invoice_amount: float = Field(
        description="组内发票含税金额合计，单位元"
    )
    group_total_paid_amount: float = Field(
        description="组内银行实际支付金额合计，单位元"
    )
    invoice_difference: float = Field(
        description="组内差额 = 发票含税总额 - 凭证账面总额，负值表示发票不足"
    )
    match_reasoning: str = Field(
        description=(
            "AI 对账逻辑说明：解释三方金额是否自洽，"
            "差额的可能原因（如少开发票、预付款、跨期等），"
            "以及是否存在异常需人工复核"
        )
    )