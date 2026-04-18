# -*- coding: utf-8 -*-
"""
infra/llm_client.py
职责：封装所有对 Doubao LLM API 的底层 HTTP 调用
输入：base64图片或文本 prompt + 认证参数
输出：LLM 返回的原始文本字符串

核心设计：
    - 指数退避重试（Exponential Backoff）：专门处理 429 限流
    - 视觉/文本两个端点分离：对应不同的 payload 结构
    - 不做任何业务逻辑，只负责网络通信的可靠性
"""
import time
import requests

DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def _post_with_retry(
    payload: dict,
    api_key: str,
    max_retries: int,
) -> str:
    """
    带指数退避的 HTTP POST。

    契约：
        输入：完整的请求 payload、API Key、最大重试次数
        输出：LLM 返回的原始文本字符串
    异常：
        requests.HTTPError：非 429 的 HTTP 错误（如 401、500），不重试
        RuntimeError：超出最大重试次数（全部因限流失败）
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries):
        response = requests.post(DOUBAO_BASE_URL, headers=headers, json=payload)

        if response.status_code == 429:
            wait_time = 2 ** attempt
            print(f"    [限流] 触发 429，{wait_time}s 后重试（第 {attempt + 1}/{max_retries} 次）")
            time.sleep(wait_time)
            continue

        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    raise RuntimeError(f"超出最大重试次数（{max_retries}），请求持续被限流")


def call_doubao_vision(
    base64_img: str,
    api_key: str,
    model_endpoint: str,
    prompt: str,
    max_retries: int = 4,
) -> str:
    """
    调用视觉模型：图片 + 文本 prompt → 文本响应。

    契约：
        输入：合法的 base64 图片字符串（不含 data:image 前缀）
        输出：LLM 原始文本（未经任何解析）
    """
    payload = {
        "model": model_endpoint,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_img}"
                        },
                    },
                ],
            }
        ],
    }
    return _post_with_retry(payload, api_key, max_retries)


def call_doubao_text(
    api_key: str,
    model_endpoint: str,
    prompt: str,
    max_retries: int = 4,
) -> str:
    """
    调用纯文本模型：文本 prompt → 文本响应。

    契约：
        输入：任意文本 prompt
        输出：LLM 原始文本（未经任何解析）
    """
    payload = {
        "model": model_endpoint,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }
    return _post_with_retry(payload, api_key, max_retries)