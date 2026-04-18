# -*- coding: utf-8 -*-
"""
schemas/voucher.py — 转账凭证


"""
from typing import List
from pydantic import BaseModel, Field
from decimal import Decimal

class VoucherLineItemSchema(BaseModel):
    subject_code: str | None = Field(
        default=None,
        description="借方科目代码，如 5001、1122，只取借方行",
    )
    subject_name: str | None = Field(
        default=None,
        description="借方科目名称，如'开发成本'、'应交税费'，只取借方行",
    )
    payee: str | None = Field(
        default=None,
        description="收款单位全称，用于与发票销售方、银行回单收款人交叉比对",
    )
    book_amount: Decimal | None = Field(
        default=None,
        description="账面金额，绝对值正数，单位元",
    )


class AccountingVoucherSchema(BaseModel):
    line_items: List[VoucherLineItemSchema] = Field(
        default_factory=list,
        description="借方成本明细列表，每行一条，贷方行不提取",
    )