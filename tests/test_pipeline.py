# -*- coding: utf-8 -*-
"""
test_pipeline.py — run_pipeline 状态机行为测试

四条命题各自证明一个状态转移触发器：
    命题 1 — 无效类型：classify 返回未知值 → extract 完全不被调用
    命题 2 — 新凭证边界：第二张凭证触发 flush → 第一组进 results
    命题 3 — 提取失败：RuntimeError → 该页跳过，pipeline 不中断
    命题 4 — 尾部 flush：图片耗尽，剩余数据强制进 results

mock 策略：
    patch classify_image     → 控制每张图片的分类结果
    patch extract_with_retry → 控制提取结果或抛出异常
    patch match_financial_documents → 观察 flush 是否发生、发生几次
"""
import pytest
from unittest.mock import patch, MagicMock, call

from pipeline import run_pipeline
from schemas.voucher import AccountingVoucherSchema
from schemas.invoice import InvoiceSchema
from schemas.match import MatchedTransactionGroupSchema


# ─────────────────────────────────────────────
# 辅助工厂：造假的 Schema 实例
# MagicMock(spec=X) 能通过 isinstance 检查，行为和真实对象一致
# ─────────────────────────────────────────────
def fake_voucher() -> AccountingVoucherSchema:
    return MagicMock(spec=AccountingVoucherSchema)

def fake_invoice() -> InvoiceSchema:
    return MagicMock(spec=InvoiceSchema)

def fake_match_result() -> MatchedTransactionGroupSchema:
    return MagicMock(spec=MatchedTransactionGroupSchema)


# ══════════════════════════════════════════════
# 命题 1：无效类型 → extract 完全不被调用
# 你的断言：call_count == 0
# ══════════════════════════════════════════════

def test_unrecognized_doc_type_skips_extraction():
    """
    classify 返回不在 _VALID_DOC_TYPES 的值。
    断言：
        - extract_with_retry 的 call_count == 0（分类失败直接 continue）
        - results 为空列表（没有任何数据进入流程）
    """
    with patch("pipeline.classify_image", return_value="未知类型"), \
         patch("pipeline.extract_with_retry") as mock_extract, \
         patch("pipeline.match_financial_documents"):

        results = run_pipeline(["fake_img_1", "fake_img_2"])

    # 核心断言：无效类型在 classify 之后立即 continue，extract 不应被碰到
    assert mock_extract.call_count == 0
    # 附加断言：没有数据流入，results 必然为空
    assert results == []


# ══════════════════════════════════════════════
# 命题 2：新凭证触发 flush → 第一组进 results，新组重新开始
# 你的断言：results 有一组数据
# 补强：match 被调用恰好 1 次（flush 只发生一次）
# ══════════════════════════════════════════════

def test_new_voucher_flushes_pending_group():
    """
    图片序列：[凭证A, 发票, 凭证B]
    凭证B 到来时，(凭证A + 发票) 这一组非空 → 触发 flush → 进 results。
    断言：
        - match_financial_documents 被调用恰好 1 次（只 flush 了第一组）
        - results 长度 == 1（第一组已入账，凭证B 开启的新组在尾部 flush 时才入账）
        - 最终 results 长度 == 2（尾部凭证B 也被 flush）
    """
    # 三张图片对应三次 classify 返回值
    classify_seq = ["转账凭证", "发票", "转账凭证"]
    # extract 返回对应类型的假实例
    extract_seq = [fake_voucher(), fake_invoice(), fake_voucher()]
    match_result = fake_match_result()

    with patch("pipeline.classify_image", side_effect=classify_seq), \
         patch("pipeline.extract_with_retry", side_effect=extract_seq), \
         patch("pipeline.match_financial_documents", return_value=match_result) as mock_match:

        results = run_pipeline(["img1", "img2", "img3"])

    # 凭证B 触发第一次 flush，尾部触发第二次 flush，共 2 次
    assert mock_match.call_count == 2
    # 两次 flush 产生两个对账组
    assert len(results) == 2


# ══════════════════════════════════════════════
# 命题 3：提取失败 → 该页跳过，pipeline 不中断
# 你的断言：不抛出异常，顺利继续跑
# ══════════════════════════════════════════════

def test_extraction_failure_skips_page_without_interrupting_pipeline():
    """
    图片序列：[凭证（失败）, 发票（成功）]
    第一页 extract 抛 RuntimeError → 跳过，第二页正常提取。
    断言：
        - run_pipeline 正常返回，不抛任何异常
        - extract_with_retry 被调用了 2 次（失败页不影响后续页被调用）
        - 失败的凭证不进入任何 group（results 里没有 voucher）
    """
    classify_seq = ["转账凭证", "发票"]
    # 第一次调用抛异常，第二次正常返回
    extract_seq = [RuntimeError("超出重试次数"), fake_invoice()]

    with patch("pipeline.classify_image", side_effect=classify_seq), \
         patch("pipeline.extract_with_retry", side_effect=extract_seq) as mock_extract, \
         patch("pipeline.match_financial_documents", return_value=fake_match_result()) as mock_match:

        # 核心断言：不抛异常（如果抛了，pytest 会直接标红这行）
        results = run_pipeline(["img1", "img2"])

    # extract 被调用了 2 次（两页都尝试提取了）
    assert mock_extract.call_count == 2

    # 只有发票成功了，凭证失败被跳过
    # match 仍然被调用（发票作为尾部数据被 flush）
    assert mock_match.call_count == 1

    # 第一个参数（vouchers 列表）为空，证明失败的凭证没有混入
    flushed_vouchers = mock_match.call_args_list[0].args[0]
    assert flushed_vouchers == []


# ══════════════════════════════════════════════
# 命题 4：图片耗尽 → 尾部 flush
# 你的断言：执行 match 后三种 list 都为空
# 补强：外部可观测的是 results 长度和 match 调用次数，内部 list 不可达
# ══════════════════════════════════════════════

def test_trailing_documents_flushed_at_end():
    """
    图片序列：[凭证, 发票]（只有一组，没有第二张凭证触发 flush）
    遍历结束后，尾部 if 分支强制 flush 剩余数据。
    断言：
        - match_financial_documents 被调用恰好 1 次（只有尾部 flush，无中途 flush）
        - results 长度 == 1（尾部数据形成一个完整的对账组）
    """
    classify_seq = ["转账凭证", "发票"]
    extract_seq = [fake_voucher(), fake_invoice()]

    with patch("pipeline.classify_image", side_effect=classify_seq), \
         patch("pipeline.extract_with_retry", side_effect=extract_seq), \
         patch("pipeline.match_financial_documents", return_value=fake_match_result()) as mock_match:

        results = run_pipeline(["img1", "img2"])

    # 没有第二张凭证触发中途 flush，match 只在尾部被调用一次
    assert mock_match.call_count == 1
    # 尾部 flush 产生一个对账组
    assert len(results) == 1