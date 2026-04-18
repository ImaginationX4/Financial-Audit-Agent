# -*- coding: utf-8 -*-
"""
pipeline.py — 核心调度状态机
职责：按业务边界将图片流切割成事务组，驱动三个 Agent 协作完成对账
输入：base64 图片列表（顺序即扫描顺序）
输出：MatchedTransactionGroupSchema 列表

核心设计：有限状态机（FSM）
    状态：当前累积的 (vouchers, invoices, receipts) 三元组
    转移触发器：遇到新的【转账凭证】→ 刷新当前组，开启新组
    终止条件：图片列表遍历完毕 → 强制刷新尾部剩余数据
。
"""
from typing import List

from agents.classifier import classify_image
from agents.extractor import extract_with_retry
from agents.matcher import match_financial_documents
from schemas.voucher import AccountingVoucherSchema
from schemas.invoice import InvoiceSchema
from schemas.bank import BankReceiptSchema
from schemas.match import MatchedTransactionGroupSchema

# 合法文档类型集合，用于过滤无法识别的图片
_VALID_DOC_TYPES = {"转账凭证", "发票", "银行转账单"}


def _flush_group(
    vouchers: List[AccountingVoucherSchema],
    invoices: List[InvoiceSchema],
    receipts: List[BankReceiptSchema],
) -> MatchedTransactionGroupSchema:
    """
    刷新当前事务组：将三类单据送入匹配 Agent，返回对账结果。

    契约：
        输入：三个列表，允许部分为空（现实中可能缺少某类单据）
        输出：MatchedTransactionGroupSchema 实例
    """
    print(f"  -> 聚合对账：{len(vouchers)} 张凭证 / {len(invoices)} 张发票 / {len(receipts)} 张回单")
    return match_financial_documents(vouchers, invoices, receipts)


def run_pipeline(base64_images: List[str]) -> List[MatchedTransactionGroupSchema]:
    """
    主调度入口：驱动完整的分类 → 提取 → 对账流水线。

    状态机逻辑：
        当前组 = (current_vouchers, current_invoices, current_receipts)
        遇到新凭证且当前组非空 → flush 当前组 → 清空 → 新凭证加入新组
        遍历结束且当前组非空 → flush 尾部组

    异常：
        单张图片提取失败（超出重试次数）→ 打印错误并跳过，不中断整体流程
    """
    current_vouchers: List[AccountingVoucherSchema] = []
    current_invoices: List[InvoiceSchema] = []
    current_receipts: List[BankReceiptSchema] = []
    results: List[MatchedTransactionGroupSchema] = []

    for page_index, base64_img in enumerate(base64_images, start=1):
        print(f"\n[第 {page_index}/{len(base64_images)} 页] 分类中...")

        # --- 阶段 1：分类 ---
        doc_type = classify_image(base64_img)
        if doc_type not in _VALID_DOC_TYPES:
            print(f"  -> 无法识别（返回：{doc_type!r}），跳过")
            continue
        print(f"  -> 识别为：{doc_type}")

        # --- 状态转移：新凭证触发 flush ---
        has_pending = current_vouchers or current_invoices or current_receipts
        if doc_type == "转账凭证" and has_pending:
            print("  -> 检测到新业务边界，刷新上一组...")
            group = _flush_group(current_vouchers, current_invoices, current_receipts)
            results.append(group)
            current_vouchers.clear()
            current_invoices.clear()
            current_receipts.clear()

        # --- 阶段 2：提取 ---
        print(f"  -> 提取结构化数据...")
        try:
            extracted = extract_with_retry(base64_img, doc_type, max_retries=2)
        except RuntimeError as e:
            print(f"  -> [跳过] 提取失败，已超出重试次数：{e}")
            continue

        # --- 路由到对应列表 ---
        if doc_type == "转账凭证":
            current_vouchers.append(extracted)
        elif doc_type == "发票":
            current_invoices.append(extracted)
        elif doc_type == "银行转账单":
            current_receipts.append(extracted)

    # --- 终止：flush 尾部剩余数据 ---
    if current_vouchers or current_invoices or current_receipts:
        print("\n[结束] 刷新尾部剩余数据...")
        group = _flush_group(current_vouchers, current_invoices, current_receipts)
        results.append(group)

    print(f"\n✅ Pipeline 完成，共生成 {len(results)} 个对账组")
    return results