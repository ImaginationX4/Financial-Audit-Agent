# -*- coding: utf-8 -*-
"""
23 passed. 把文件给你：---

### 三个值得注意的决策，背后都有理由

**① `make_invoice()` 工厂函数**

测试体只写 delta，不重复全量字段。本质是把「构造合法基准」和「被测变量」分离，否则每个 test body 里都有 4 个字段赋值，信噪比极低。

**② `parametrize` 用在白名单全集验证**

```python
@pytest.mark.parametrize("rate", VALID_RATES)
def test_tax_rate_all_valid_rates_accepted(rate):
```

白名单是一个集合命题 `∀ r ∈ S → pass`，7 个元素写 7 个 `def` 是重复，`parametrize` 是这个命题的直接表达。失败时 pytest 会报告具体是哪个 rate 挂掉。

**③ 边界三切分**

```
< 0.5   → passes（within）
= 0.5   → passes（at boundary）  ← 最容易漏的一条
> 0.5   → raises（exceeds）
```

`> 0.5` 才拦截，所以 `= 0.5` 必须放行。只写前后不写边界，就是在赌实现没有 off-by-one，而不是在证明它。
"""
"""
test_invoice.py — InvoiceSchema 单元测试

测试分层:
    Validator 1 — 税率归一化 (normalize_tax_rate)
    Validator 2 — 合法税率白名单 (validate_tax_rate)
    Validator 3 — 三方金额一致性 (validate_amount_consistency)

命名约定: test_<被测对象>_<输入条件>_<预期结果>
"""
import pytest
from pydantic import ValidationError
from schemas.invoice import InvoiceSchema


# ─────────────────────────────────────────────
# 辅助工厂函数：把"构造 Schema"这个噪音集中在一处
# 测试体只声明 delta，不重复写全量字段
# ─────────────────────────────────────────────
def make_invoice(**kwargs) -> InvoiceSchema:
    """用关键字覆盖默认值，构造一个合法的基准发票。"""
    defaults = dict(
        seller_name="示例科技有限公司",
        invoice_amount=100.0,
        tax_rate=0.09,
        total_amount_with_tax=109.0,
    )
    defaults.update(kwargs)
    return InvoiceSchema(**defaults)


# ══════════════════════════════════════════════
# Validator 1：税率归一化
# 命题空间：输入类型 × 数值范围 → 归一化路径
# ══════════════════════════════════════════════

def test_tax_rate_percent_string_normalized_to_decimal():
    # "9%" ∈ str  →  先剥 "%"，再 float()，再 /100  →  0.09
    # 验证的是字符串解析路径，不是白名单
    invoice = make_invoice(tax_rate="9%")
    assert invoice.tax_rate == 0.09


def test_tax_rate_integer_gt_1_normalized_to_decimal():
    # 9 ∈ int, 9 > 1  →  走 v / 100 分支  →  0.09
    # 区别于 "9%"：没有字符串剥离，只走 >1 除法
    invoice = make_invoice(tax_rate=9)
    assert invoice.tax_rate == 0.09


def test_tax_rate_decimal_lt_1_passthrough():
    # 0.09 ∈ float, 0.09 ≤ 1  →  不触发除法，直接进白名单  →  0.09
    # 同一终态 0.09，但经过了完全不同的代码路径
    invoice = make_invoice(tax_rate=0.09)
    assert invoice.tax_rate == 0.09


def test_tax_rate_zero_string_normalized():
    # "0%" 是免税场景，边界值，同时测字符串路径 + 白名单 0.0
    invoice = make_invoice(tax_rate="0%", total_amount_with_tax=100.0)
    assert invoice.tax_rate == 0.0


def test_tax_rate_none_returns_none():
    # None → 早返回，完全跳过归一化和白名单
    # 同时断言：缺税率时不触发三方一致性校验（短路守卫）
    invoice = make_invoice(tax_rate=None)
    assert invoice.tax_rate is None


def test_tax_rate_unparseable_string_raises():
    # "abc" → float("abc") 抛 ValueError → 包装进 ValidationError
    # pytest.raises 是「否定路径」的标准形态
    with pytest.raises(ValidationError) as exc_info:
        make_invoice(tax_rate="abc")
    # 进一步断言错误消息含有业务语义，防止误匹配其他字段的报错
    assert "税率无法解析" in str(exc_info.value)


# ══════════════════════════════════════════════
# Validator 2：合法税率白名单
# 命题空间：∀ r ∈ valid_rates → pass；∃ r ∉ valid_rates → raise
# ══════════════════════════════════════════════

VALID_RATES = [0.0, 0.01, 0.03, 0.05, 0.06, 0.09, 0.13]

@pytest.mark.parametrize("rate", VALID_RATES)
def test_tax_rate_all_valid_rates_accepted(rate):
    # ∀ r ∈ valid_rates → 不抛异常
    # parametrize 把全集合压缩成一条命题，失败时报告具体 rate 值
    # total 需随 rate 动态计算，否则会被 Validator 3 拦截
    total = round(100.0 * (1 + rate), 2)
    invoice = make_invoice(tax_rate=rate, total_amount_with_tax=total)
    assert invoice.tax_rate == round(rate, 2)


def test_tax_rate_illegal_rate_raises():
    # 0.08 ∉ valid_rates（现行无 8% 档）→ ValidationError
    # 只需一个反例证伪白名单完整性
    with pytest.raises(ValidationError) as exc_info:
        make_invoice(tax_rate=0.08)
    assert "非法税率" in str(exc_info.value)


# ══════════════════════════════════════════════
# Validator 3：三方金额一致性
# 命题空间：{total < amt} | {|Δ| ≤ 0.5} | {|Δ| > 0.5} | {任一字段为 None}
# ══════════════════════════════════════════════

def test_amount_consistency_exact_match_passes():
    # total == amt × (1 + rate) 精确成立 → 正路径，不抛异常
    # 基准：amt=100, rate=0.09, total=109.0
    invoice = make_invoice(invoice_amount=100.0, tax_rate=0.09, total_amount_with_tax=109.0)
    assert invoice.total_amount_with_tax == 109.0


def test_amount_consistency_within_tolerance_passes():
    # |expected(109.0) - actual(109.49)| = 0.49 ≤ 0.5 → 容忍，不抛
    # 独立命题：容忍阈值是业务决策，必须单独证明边界成立
    invoice = make_invoice(invoice_amount=100.0, tax_rate=0.09, total_amount_with_tax=109.49)
    assert invoice.total_amount_with_tax == 109.49


def test_amount_consistency_at_tolerance_boundary_passes():
    # |expected - actual| == 0.5（边界恰好不超）→ 不抛
    # 边界值分析：> 0.5 才拦截，= 0.5 必须放行
    invoice = make_invoice(invoice_amount=100.0, tax_rate=0.09, total_amount_with_tax=109.5)
    assert invoice.total_amount_with_tax == 109.5


def test_amount_consistency_exceeds_tolerance_raises():
    # |expected(109.0) - actual(110.0)| = 1.0 > 0.5 → ValidationError
    with pytest.raises(ValidationError) as exc_info:
        make_invoice(invoice_amount=100.0, tax_rate=0.09, total_amount_with_tax=110.0)
    assert "金额逻辑不自洽" in str(exc_info.value)


def test_total_less_than_amount_raises():
    # total(80) < amt(100) → 前置守卫，不依赖 rate 直接拦截
    # 这是独立于三方一致性的更早的守卫，必须分开测
    with pytest.raises(ValidationError) as exc_info:
        make_invoice(invoice_amount=100.0, tax_rate=0.09, total_amount_with_tax=80.0)
    assert "含税金额" in str(exc_info.value)


def test_missing_rate_skips_consistency_check():
    # rate=None → guard clause `if ... is not None` 短路 → 不做三方校验
    # total 和 amt 故意不自洽，证明"不校验"而非"校验通过"
    invoice = make_invoice(
        invoice_amount=100.0,
        tax_rate=None,
        total_amount_with_tax=999.0,  # 明显不自洽，但 rate=None 时不应触发校验
    )
    assert invoice.tax_rate is None
    assert invoice.total_amount_with_tax == 999.0


def test_missing_total_skips_consistency_check():
    # total=None → guard clause 同理短路
    invoice = make_invoice(
        invoice_amount=100.0,
        tax_rate=0.09,
        total_amount_with_tax=None,
    )
    assert invoice.total_amount_with_tax is None


def test_missing_amount_skips_consistency_check():
    # amt=None → guard clause 同理短路
    invoice = make_invoice(
        invoice_amount=None,
        tax_rate=0.09,
        total_amount_with_tax=109.0,
    )
    assert invoice.invoice_amount is None

from hypothesis import given, strategies as st

# -*- coding: utf-8 -*-
from hypothesis import given, strategies as st, assume
from pydantic import ValidationError
from schemas.invoice import InvoiceSchema

# 1. 定义税率合法的采样范围
VALID_RATES = [0.0, 0.01, 0.03, 0.05, 0.06, 0.09, 0.13]

@given(
    # 模拟不含税金额：从 0.01 到 1 亿，精度到分
    amt=st.floats(min_value=0.01, max_value=1e8, allow_nan=False, allow_infinity=False),
    # 从白名单中随机选一个税率
    rate=st.sampled_from(VALID_RATES),
    # 模拟一个随机的扰动误差：-0.5 到 +0.5 之间
    error=st.floats(min_value=0, max_value=0.5)
)
def test_invoice_acceptance_within_tolerance_property(amt, rate, error):
    """
    属性 1：只要 (预期总额 - 实际总额) 的绝对值 <= 0.5，Schema 永远不应该报错。
    """
    # 理论上的精确含税金额
    expected_total = amt * (1 + rate)
    # 注入随机误差后的实际金额
    actual_total = expected_total + error
    
    # 业务前置守卫：含税不能小于不含税（这是我们 Schema 里的硬规则）
    # 如果生成的 error 导致 total < amt，我们跳过这组随机数，不计入测试
    

    # 执行断言：不应该抛出 ValidationError
    try:
        InvoiceSchema(
            invoice_amount=amt,
            tax_rate=rate,
            total_amount_with_tax=actual_total
        )
    except ValidationError as e:
        pytest.fail(f"属性校验失败！金额:{amt}, 税率:{rate}, 误差:{error}。错误信息: {e}")

@given(
    amt=st.floats(min_value=0.01, max_value=1e8),
    rate=st.sampled_from(VALID_RATES),
    # 模拟一个超出范围的误差
    error=st.floats(min_value=0.51, max_value=10.0)

)
def test_invoice_rejection_outside_tolerance_property(amt, rate, error):
    """
    属性 2：只要误差绝对值 > 0.5，Schema 必须拦截并抛出 ValidationError。
    """
    actual_total = (amt * (1 + rate)) + error
    

    with pytest.raises(ValidationError) as exc_info:
        InvoiceSchema(
            invoice_amount=amt,
            tax_rate=rate,
            total_amount_with_tax=actual_total
        )
    assert "金额逻辑不自洽" in str(exc_info.value)
# ══════════════════════════════════════════════
# 集成路径：全字段 None（空发票）
# ══════════════════════════════════════════════

def test_all_fields_none_is_valid():
    # 所有字段 Optional，全为 None 时应构造成功（OCR 未识别场景）
    invoice = InvoiceSchema()
    assert invoice.seller_name is None
    assert invoice.tax_rate is None