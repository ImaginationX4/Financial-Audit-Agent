# -*- coding: utf-8 -*-
"""
client.py — 提交 PDF 对账任务，轮询状态，下载结果
用法：python client.py path/to/your.pdf
"""
import sys
import time
import json
import requests

from pdf_to_base64 import pdf_to_base64_images

BASE_URL = "http://localhost:6006"
POLL_INTERVAL = 5  # 秒


def submit(base64_images: list[str]) -> str:
    """提交任务，返回 task_id。"""
    resp = requests.post(
        f"{BASE_URL}/api/submit",
        json={"images_base64": base64_images},
    )
    resp.raise_for_status()
    task_id = resp.json()["task_id"]
    print(f"✅ 任务已提交，task_id = {task_id}")
    return task_id


def poll(task_id: str) -> dict:
    """轮询状态，直到 completed 或 failed。"""
    while True:
        resp = requests.get(f"{BASE_URL}/api/status", params={"task_id": task_id})
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        print(f"  状态：{status}")

        if status == "completed":
            return data
        if status == "failed":
            raise RuntimeError(f"任务失败：{data.get('error')}")

        time.sleep(POLL_INTERVAL)


def download(task_id: str) -> list[dict]:
    """下载对账结果。"""
    resp = requests.get(f"{BASE_URL}/api/result", params={"task_id": task_id})
    resp.raise_for_status()
    return resp.json()


def main(pdf_path: str) -> None:
    # 1. PDF → base64
    print(f"\n=== 读取 PDF：{pdf_path} ===")
    base64_images = pdf_to_base64_images(pdf_path)
    print(f"共 {len(base64_images)} 页")

    # 2. 提交
    print("\n=== 提交任务 ===")
    task_id = submit(base64_images)

    # 3. 轮询
    print(f"\n=== 轮询状态（每 {POLL_INTERVAL}s）===")
    poll(task_id)

    # 4. 下载结果
    print("\n=== 下载结果 ===")
    result = download(task_id)

    # 5. 保存到本地
    out_path = pdf_path.replace(".pdf", "_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✅ 结果已保存：{out_path}")


if __name__ == "__main__":
   
    if len(sys.argv) != 2:
        print("用法：python client.py path/to/your.pdf")
        sys.exit(1)
    main(sys.argv[1])