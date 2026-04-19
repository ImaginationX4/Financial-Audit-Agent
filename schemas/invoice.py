# -*- coding: utf-8 -*-
"""
schemas/invoice.py — 发票


"""
from pydantic import BaseModel, Field, field_validator, model_validator


class InvoiceSchema(BaseModel):
    seller_name: str | None = Field(
        default=None,
        description="销售方（收款单位）全称，用于与凭证 payee 字段交叉比对",
    )
    invoice_amount: float | None = Field(
        default=None,
        description="不含税金额，单位元，正数",
    )
    tax_rate: str | float | None = Field(
        default=None,
        description="税率，接受'9%'或 0.09 两种格式，内部统一归一化为小数",
    )
    total_amount_with_tax: float | None = Field(
        default=None,
        description="含税金额（价税合计），单位元，正数",
    )

    # --- Validator 1：税率归一化 ---
    # LLM 有时返回 "9%"，有时返回 0.09，必须统一为小数，否则后续计算全部错乱
    @field_validator("tax_rate", mode="before")
    @classmethod
    def normalize_tax_rate(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip().replace("%", "")
            try:
                v = float(v)
            except ValueError:
                raise ValueError(f"税率无法解析为数字: {v!r}")
        # 百分比形式 → 小数（9 → 0.09）
        if v > 1:
            v = v / 100

        # --- Validator 2：合法税率白名单 ---
        # 中国现行增值税率：0% / 1% / 3% / 5% / 6% / 9% / 13%
        # 任何不在此列的值都是 LLM 幻觉或识别错误
        valid_rates = {0.0, 0.01, 0.03, 0.05, 0.06, 0.09, 0.13}
        if round(v, 2) not in valid_rates:
            raise ValueError(
                f"非法税率 {v}，中国现行增值税率仅限：{sorted(valid_rates)}"
            )
        return round(v, 2)

    # --- Validator 3：三方金额一致性 ---
    # 在所有 field_validator 执行后运行，此时 tax_rate 已归一化
    # 容忍 0.5 元误差：发票打印时存在四舍五入
    @model_validator(mode="after")
    def validate_amount_consistency(self):
        amt = self.invoice_amount
        total = self.total_amount_with_tax
        rate = self.tax_rate

        if amt is not None and total is not None:
            if total < amt:
                raise ValueError(
                    f"含税金额({total}) < 不含税金额({amt})，数据异常"
                )

        if amt is not None and total is not None and rate is not None:
            expected = round(amt * (1 + rate), 2)
            actual = round(total, 2)
            if abs(expected - actual) > 0.5:
                raise ValueError(
                    f"金额逻辑不自洽：{amt} × (1 + {rate}) = {expected}，"
                    f"但含税金额为 {actual}，差额 {abs(expected - actual):.2f} 超出容忍范围（0.5元）"
                )
        return self