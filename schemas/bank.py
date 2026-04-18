# -*- coding: utf-8 -*-

from pydantic import BaseModel, Field
from decimal import Decimal

class BankReceiptSchema(BaseModel):
    payee_account_name: str | None = Field(
        default=None,
        description="收款人全称，用于与凭证 payee、发票 seller_name 三方交叉比对",
    )
    actual_paid_amount: Decimal | None = Field(
        default=None,
        description="银行实际扣款金额，单位元，正数",
    )