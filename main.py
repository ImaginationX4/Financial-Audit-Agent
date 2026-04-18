# -*- coding: utf-8 -*-
"""
main.py — FastAPI Web 服务
职责：将 pipeline 包装为异步任务队列，提供提交、查询、下载三个端点
输入：base64 图片列表（POST JSON）
输出：任务 ID → 轮询状态 → 下载 JSON 结果

核心设计：
    - 异步提交（Fire-and-forget）：pipeline 耗时长，不能同步阻塞请求
    - 内存任务表（tasks_db）：轻量，无需引入 Redis/Celery
    - 结果序列化为 JSON：与 exporter 解耦，展示层由调用方决定

端点设计：
    POST /api/submit   → 提交任务，返回 task_id
    GET  /api/status   → 查询任务状态（pending / processing / completed / failed）
    GET  /api/result   → 下载对账结果 JSON（仅 completed 状态可用）
"""
import uuid
import json
from typing import Dict, List

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline import run_pipeline
from schemas.match import MatchedTransactionGroupSchema


app = FastAPI(title="Financial Audit Agent")

# 内存任务表：task_id → { status, result?, error? }
# 生产环境应替换为 Redis，此处保持轻量
tasks_db: Dict[str, dict] = {}


# ==========================================
# Request / Response Models
# ==========================================

class SubmitRequest(BaseModel):
    images_base64: List[str]


# ==========================================
# 后台任务
# ==========================================

def _run_pipeline_task(task_id: str, base64_list: List[str]) -> None:
    """
    后台执行体：运行 pipeline 并将结果写入 tasks_db。

    契约：
        不抛出异常（所有错误转为 failed 状态写入 tasks_db）
        结果以 JSON 可序列化的 list[dict] 存储
    """
    try:
        tasks_db[task_id]["status"] = "processing"

        groups: List[MatchedTransactionGroupSchema] = run_pipeline(base64_list)

        # Pydantic → dict，保证 JSON 可序列化
        tasks_db[task_id] = {
            "status": "completed",
            "result": [g.model_dump(mode="json") for g in groups],
        }

    except Exception as e:
        tasks_db[task_id] = {
            "status": "failed",
            "error": str(e),
        }
        print(f"[CRITICAL] 后台任务 {task_id} 崩溃：{e}")


# ==========================================
# 端点
# ==========================================

@app.post("/api/submit", status_code=202)
async def submit_task(
    request: SubmitRequest,
    background_tasks: BackgroundTasks,
):
    """
    提交对账任务。

    返回：{ task_id, status: "pending" }
    HTTP 202 Accepted：任务已接受但尚未完成
    """
    task_id = str(uuid.uuid4())
    tasks_db[task_id] = {"status": "pending"}
    background_tasks.add_task(_run_pipeline_task, task_id, request.images_base64)
    return {"task_id": task_id, "status": "pending"}


@app.get("/api/status")
async def check_status(task_id: str):
    """
    查询任务状态。

    返回：
        pending / processing → { task_id, status }
        completed            → { task_id, status, result_url }
        failed               → { task_id, status, error }
    """
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    task = tasks_db[task_id]
    status = task["status"]

    if status == "completed":
        return {
            "task_id": task_id,
            "status": "completed",
            "result_url": f"/api/result?task_id={task_id}",
        }
    if status == "failed":
        return {
            "task_id": task_id,
            "status": "failed",
            "error": task.get("error", "unknown error"),
        }

    return {"task_id": task_id, "status": status}


@app.get("/api/result")
async def get_result(task_id: str):
    """
    下载对账结果（仅 completed 状态可用）。

    返回：MatchedTransactionGroupSchema list 的 JSON 序列化结果
    """
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    task = tasks_db[task_id]
    if task["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task is not completed yet, current status: {task['status']}",
        )

    return JSONResponse(content=task["result"])


# ==========================================
# 启动入口
# ==========================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=6006)