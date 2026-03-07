"""
ziwei/api/server.py
FastAPI 服务入口 - MVP 版本
"""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("ziwei.api")


class TaskRequest(BaseModel):
    instruction: str = Field(..., description="任务指令")
    context: Optional[Dict[str, Any]] = Field(default=None, description="额外上下文")
    complexity: Optional[str] = Field(
        default="medium", description="复杂度: simple/medium/complex"
    )


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    instruction: str
    result: Optional[str] = None
    confidence: Optional[float] = None
    version_id: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


class HealthCheckResponse(BaseModel):
    status: str
    version: str
    components: Dict[str, str]


tasks_store: Dict[str, Dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 ZiWei API 启动")
    yield
    logger.info("👋 ZiWei API 关闭")


app = FastAPI(
    title="ZiWei API",
    description="Multi-Agent 协同平台 - MVP",
    version="0.1.0",
    lifespan=lifespan,
)


def get_runner():
    """获取运行器（延迟初始化）"""
    from ..ziwei import ZiWeiRunner

    if not hasattr(app.state, "runner"):
        app.state.runner = ZiWeiRunner()
    return app.state.runner


@app.get("/", response_model=Dict[str, str])
async def root():
    return {
        "name": "ZiWei - 紫微",
        "version": "0.1.0",
        "description": "Multi-Agent 协同平台",
    }


@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """健康检查"""
    return HealthCheckResponse(
        status="healthy",
        version="0.1.0",
        components={
            "api": "ok",
            "l0_brain": "pending",
            "l1_executor": "pending",
            "version_store": "pending",
        },
    )


@app.post("/task", response_model=TaskResponse)
async def create_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """提交任务"""
    task_id = str(uuid.uuid4())

    task_info = {
        "task_id": task_id,
        "instruction": request.instruction,
        "context": request.context or {},
        "complexity": request.complexity,
        "status": "pending",
        "result": None,
        "confidence": None,
        "version_id": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    tasks_store[task_id] = task_info

    background_tasks.add_task(run_task_background, task_id, request)

    return TaskResponse(
        task_id=task_id,
        status="pending",
        message="任务已提交，正在处理",
    )


async def run_task_background(task_id: str, request: TaskRequest):
    """后台执行任务"""
    from ..core.models import Task, TaskComplexity
    from ..core.enums import TaskStatus
    from ..agents.l0.brain import L0Brain
    from ..agents.l1.executor import L1Executor
    from ..agents.l1.validator import SimpleValidator
    from ..agents.l1.tiebreaker import SimpleTiebreaker
    from ..ziwei import DummyWorkerPool, DummyClient, DummyEmbedder

    runner = get_runner()

    try:
        await runner.initialize()

        complexity_map = {
            "simple": TaskComplexity.SIMPLE,
            "medium": TaskComplexity.MEDIUM,
            "complex": TaskComplexity.COMPLEX,
        }

        task = Task(
            id=task_id,
            instruction=request.instruction,
            context=request.context or {},
            complexity=complexity_map.get(request.complexity, TaskComplexity.MEDIUM),
            status=TaskStatus.RUNNING,
        )

        l1_executor = L1Executor(
            instance_id="l1_executor_001",
            adapter=runner.l1_adapter,
            worker_pool=DummyWorkerPool(),
            version_store=runner.version_store,
            l0_client=DummyClient(),
            notifier=runner.notifier,
            manifest_secret=runner.settings.manifest_secret,
        )

        validator = SimpleValidator("validator_001")
        tiebreaker = SimpleTiebreaker("tiebreaker_001")

        l0_brain = L0Brain(
            adapter=runner.l0_adapter,
            l1_executor=l1_executor,
            l1_validator=validator,
            l1_tiebreaker=tiebreaker,
            version_store=runner.version_store,
            notifier=runner.notifier,
            embedder=DummyEmbedder(),
        )

        result = await l0_brain.run(task)

        tasks_store[task_id].update(
            {
                "status": "completed",
                "result": result.content,
                "confidence": result.confidence,
                "version_id": result.version_id,
                "updated_at": datetime.utcnow().isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"任务 {task_id} 执行失败: {e}", exc_info=True)
        tasks_store[task_id].update(
            {
                "status": "failed",
                "error": str(e),
                "updated_at": datetime.utcnow().isoformat(),
            }
        )


@app.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """查询任务状态"""
    if task_id not in tasks_store:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks_store[task_id]
    return TaskStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        instruction=task["instruction"],
        result=task.get("result"),
        confidence=task.get("confidence"),
        version_id=task.get("version_id"),
        error=task.get("error"),
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


@app.get("/task/{task_id}/trace")
async def get_task_trace(task_id: str):
    """查询任务执行链路"""
    if task_id not in tasks_store:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks_store[task_id]
    return {
        "task_id": task_id,
        "status": task["status"],
        "trace": task.get("trace", {}),
        "verified_by": task.get("verified_by", []),
        "audit_scores": task.get("audit_scores", {}),
    }


@app.get("/versions/{task_id}")
async def get_versions(task_id: str):
    """查询任务版本历史"""
    runner = get_runner()
    try:
        versions = await runner.version_store.get_versions(task_id)
        return {
            "task_id": task_id,
            "versions": [
                {
                    "version_id": v.version_id,
                    "agent_id": v.agent_id,
                    "artifact_type": v.artifact_type,
                    "score": v.score,
                    "status": v.status,
                    "timestamp": v.timestamp.isoformat(),
                }
                for v in versions
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/human/approve/{task_id}")
async def human_approve(task_id: str, request: BaseModel):
    """人工审批"""
    approved = request.approved if hasattr(request, "approved") else True
    reason = request.reason if hasattr(request, "reason") else ""

    logger.info(f"人工审批 task_id={task_id}: approved={approved}, reason={reason}")

    return {
        "task_id": task_id,
        "approved": approved,
        "token": f"manual_approved_{task_id}" if approved else None,
    }


@app.get("/stats")
async def get_stats():
    """获取统计信息"""
    total = len(tasks_store)
    completed = sum(1 for t in tasks_store.values() if t["status"] == "completed")
    failed = sum(1 for t in tasks_store.values() if t["status"] == "failed")
    pending = sum(
        1 for t in tasks_store.values() if t["status"] in ["pending", "running"]
    )

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "pending": pending,
    }
