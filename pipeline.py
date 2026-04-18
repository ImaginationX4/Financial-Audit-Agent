# -*- coding: utf-8 -*-
"""
pipeline.py — 核心调度状态机
职责：按业务边界将图片流切割成事务组，驱动三个 Agent 协作完成对账
输入：base64 图片列表（顺序即扫描顺序）
输出：MatchedTransactionGroupSchema 列表

核心设计：两阶段分离
    阶段 1：串行分类 + FSM 纯逻辑切割（classify 决定状态转移，必须有序）
    阶段 2：组内并行提取 + 并行匹配（extract/match 无跨图依赖，可并发）

FSM 定义：
    状态：当前累积的 (vouchers, invoices, receipts) 三元组
    转移触发器：遇到新的【转账凭证】→ 刷新当前组，开启新组
    终止条件：图片列表遍历完毕 → 强制刷新尾部剩余数据
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from agents.classifier import classify_image
from agents.extractor import extract_with_retry
from agents.matcher import match_financial_documents
from schemas.bank import BankReceiptSchema
from schemas.invoice import InvoiceSchema
from schemas.match import MatchedTransactionGroupSchema
from schemas.voucher import AccountingVoucherSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_VALID_DOC_TYPES = {"转账凭证", "发票", "银行转账单"}

_TYPE_MAP: Dict[str, str] = {
    "转账凭证": "vouchers",
    "发票": "invoices",
    "银行转账单": "receipts",
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class _ImageGroup:
    """
    一个事务组的原始图片集合（提取前）。

    vouchers / invoices / receipts 存储的是 (page_index, base64_img) 元组，
    保留 page_index 仅用于日志追踪，不参与业务逻辑。
    """
    vouchers: List[Tuple[int, str]] = field(default_factory=list)
    invoices: List[Tuple[int, str]] = field(default_factory=list)
    receipts: List[Tuple[int, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.vouchers or self.invoices or self.receipts)

    def append(self, doc_type: str, page_index: int, base64_img: str) -> None:
        key = _TYPE_MAP[doc_type]
        getattr(self, key).append((page_index, base64_img))


# ---------------------------------------------------------------------------
# 阶段 1-A：串行分类
# ---------------------------------------------------------------------------

def _classify_all(
    base64_images: List[str],
) -> List[Tuple[int, str, Optional[str]]]:
    """
    串行分类所有图片。FSM 的状态转移依赖分类顺序，此处不可并行。

    返回：[(page_index, base64_img, doc_type | None), ...]
        doc_type 为 None 表示无法识别，后续 FSM 直接跳过。
    """
    classified = []
    total = len(base64_images)
    for page_index, base64_img in enumerate(base64_images, start=1):
        logger.info("[%d/%d] 分类中...", page_index, total)
        doc_type = classify_image(base64_img)
        if doc_type not in _VALID_DOC_TYPES:
            logger.warning("  -> 无法识别（返回：%r），跳过", doc_type)
            classified.append((page_index, base64_img, None))
        else:
            logger.info("  -> 识别为：%s", doc_type)
            classified.append((page_index, base64_img, doc_type))
    return classified


# ---------------------------------------------------------------------------
# 阶段 1-B：FSM 纯逻辑切割（无 IO）
# ---------------------------------------------------------------------------

def _partition_by_fsm(
    classified: List[Tuple[int, str, Optional[str]]],
) -> List[_ImageGroup]:
    """
    纯函数：依据 FSM 规则将已分类图片切割成事务组。

    无 IO，无副作用，O(n) 时间。
    可独立单元测试，与 LLM 调用完全解耦。

    FSM 转移规则：
        - doc_type is None          → 跳过（不进入任何组）
        - doc_type == "转账凭证"
            且当前组非空            → flush 当前组，开启新组，新凭证加入新组
            且当前组为空            → 新凭证直接加入当前组
        - 其他合法类型             → 追加到当前组
        - 遍历结束且当前组非空     → flush 尾部组
    """
    groups: List[_ImageGroup] = []
    current = _ImageGroup()

    for page_index, base64_img, doc_type in classified:
        if doc_type is None:
            continue

        if doc_type == "转账凭证" and not current.is_empty():
            logger.info("  [FSM] 检测到新业务边界（第 %d 页），切割上一组", page_index)
            groups.append(current)
            current = _ImageGroup()

        current.append(doc_type, page_index, base64_img)

    if not current.is_empty():
        groups.append(current)

    return groups


# ---------------------------------------------------------------------------
# 阶段 2：并行提取 + 匹配
# ---------------------------------------------------------------------------

def _extract_one(
    page_index: int,
    base64_img: str,
    doc_type: str,
    max_retries: int = 2,
) -> Optional[object]:
    """
    提取单张图片的结构化数据。
    失败时返回 None（不中断整体流程）。
    """
    logger.info("  [提取] 第 %d 页（%s）...", page_index, doc_type)
    try:
        return extract_with_retry(base64_img, doc_type, max_retries=max_retries)
    except RuntimeError as e:
        logger.error("  [跳过] 第 %d 页提取失败，已超出重试次数：%s", page_index, e)
        return None


def _process_group(
    group_index: int,
    group: _ImageGroup,
    extract_workers: int,
) -> MatchedTransactionGroupSchema:
    """
    处理单个事务组：
        1. 组内并行提取（ThreadPoolExecutor）
        2. 汇总结果 → 送入 matcher

    group_index 仅用于日志，无业务含义。
    """
    vouchers: List[AccountingVoucherSchema] = []
    invoices: List[InvoiceSchema] = []
    receipts: List[BankReceiptSchema] = []

    # 构造所有提取任务：(page_index, base64_img, doc_type)
    tasks: List[Tuple[int, str, str]] = [
        (pi, img, "转账凭证") for pi, img in group.vouchers
    ] + [
        (pi, img, "发票") for pi, img in group.invoices
    ] + [
        (pi, img, "银行转账单") for pi, img in group.receipts
    ]

    logger.info(
        "[组 %d] 并行提取 %d 张（凭证 %d / 发票 %d / 回单 %d）",
        group_index,
        len(tasks),
        len(group.vouchers),
        len(group.invoices),
        len(group.receipts),
    )

    with ThreadPoolExecutor(max_workers=extract_workers) as executor:
        future_to_meta = {
            executor.submit(_extract_one, pi, img, dtype): (pi, dtype)
            for pi, img, dtype in tasks
        }
        for future in as_completed(future_to_meta):
            pi, dtype = future_to_meta[future]
            result = future.result()  # _extract_one 内部已捕获异常，不会抛出
            if result is None:
                continue
            if dtype == "转账凭证":
                vouchers.append(result)
            elif dtype == "发票":
                invoices.append(result)
            elif dtype == "银行转账单":
                receipts.append(result)

    logger.info(
        "  -> 聚合对账：%d 张凭证 / %d 张发票 / %d 张回单",
        len(vouchers), len(invoices), len(receipts),
    )
    return match_financial_documents(vouchers, invoices, receipts)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_pipeline(
    base64_images: List[str],
    extract_workers: int = 4,
    group_workers: int = 2,
) -> List[MatchedTransactionGroupSchema]:
    """
    主调度入口：驱动完整的分类 → 提取 → 对账流水线。

    参数：
        base64_images:   顺序即扫描顺序，不可乱序
        extract_workers: 组内并行提取的线程数（受 LLM API 并发限制）
        group_workers:   组间并行 match 的线程数

    执行模型：
        串行  classify × n          （FSM 状态转移依赖顺序）
        O(1)  _partition_by_fsm     （纯逻辑，无 IO）
        并行  extract × n           （组内 ThreadPoolExecutor）
        并行  match × k             （组间 ThreadPoolExecutor，k = 组数）

    复杂度（t = 单次 LLM 调用耗时）：
        重构前：(2n + k) · t
        重构后：(n + 1) · t        （classify 串行不可省，extract/match 并行）
    """
    if not base64_images:
        logger.warning("输入图片列表为空，直接返回")
        return []

    total = len(base64_images)
    logger.info("=== Pipeline 启动：共 %d 张图片 ===", total)

    # 阶段 1-A：串行分类（O(n·t)，不可并行）
    logger.info("--- 阶段 1-A：串行分类 ---")
    classified = _classify_all(base64_images)

    # 阶段 1-B：FSM 纯逻辑切割（O(n)，无 IO）
    logger.info("--- 阶段 1-B：FSM 切割 ---")
    groups = _partition_by_fsm(classified)
    logger.info("共切割出 %d 个事务组", len(groups))

    if not groups:
        logger.warning("未识别出任何有效事务组，返回空列表")
        return []

    # 阶段 2：组间并行 → 组内并行提取 → match
    logger.info("--- 阶段 2：并行提取 & 匹配 ---")
    results: List[Optional[MatchedTransactionGroupSchema]] = [None] * len(groups)

    with ThreadPoolExecutor(max_workers=group_workers) as executor:
        future_to_idx = {
            executor.submit(_process_group, i + 1, group, extract_workers): i
            for i, group in enumerate(groups)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                logger.error("[组 %d] match 阶段异常：%s", idx + 1, e)

    # 过滤掉异常组，保留顺序
    final = [r for r in results if r is not None]
    logger.info("=== Pipeline 完成，共生成 %d 个对账组 ===", len(final))
    return final