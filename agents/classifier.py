# -*- coding: utf-8 -*-
"""
Agent 1: 分类器
职责：识别图片是哪种财务单据类型
输入：base64 图片字符串
输出：文档类型字符串（"转账凭证" | "发票" | "银行转账单" | None）
"""
import re
from infra.llm_client import call_doubao_vision
from agents.config import CORP_API_KEY, CORP_VISION_EP


# --- 私有工具函数：从 LLM 自由文本中提取类型标签 ---
def _extract_document_type(llm_response_text: str) -> str | None:
    pattern = r"(转账凭证|发票|银行转账单)"
    match = re.search(pattern, llm_response_text)
    if match:
        return match.group(1)
    return None


def classify_image(base64_img: str) -> str | None:
    """
    调用视觉 LLM 对图片进行分类。

    契约：
        输入：合法的 base64 图片字符串
        输出：["转账凭证", "发票", "银行转账单"] 之一，或 None（无法识别）

    异常：
        RuntimeError：LLM 调用失败或超出重试次数（由 llm_client 抛出）
    """
    prompt = (
        "你是一个极其严谨的财务专家。请识别图片中的单据类型，并在以下三者中选其一：\n"
        "1.【转账凭证】\n"
        "2.【银行转账单】\n"
        "3.【发票】\n"
        "### 判定标准：\n"
        "- 转账凭证：财务软件打印的记账凭证，包含借贷科目和摘要\n"
        "- 银行转账单：银行系统出具的转账回单或汇款凭证\n"
        "- 发票：增值税发票（普票或专票）\n"
        "请直接回答类别名称，例如：【转账凭证】。"
    )

    raw_response = call_doubao_vision(
        base64_img=base64_img,
        api_key=CORP_API_KEY,
        model_endpoint=CORP_VISION_EP,
        prompt=prompt,
    )
    return _extract_document_type(raw_response)