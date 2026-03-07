"""
ziwei/infra/store.py
SQLite 版本存储实现 - MVP 阶段使用本地 SQLite
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ..core.models import ArtifactVersion, AuditResult, FinalResult


class SQLiteStore:
    """
    SQLite 版本存储实现
    MVP 阶段使用本地 SQLite 文件，避免依赖 Redis/Postgres
    """

    def __init__(self, db_path: str = ".ziwei/ziwei.db"):
        self.db_path = db_path
        self._ensure_db_dir()
        self._init_db()

    def _ensure_db_dir(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    version_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    content TEXT,
                    diff_from TEXT,
                    score REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'pending',
                    snapshot BLOB,
                    timestamp TEXT NOT NULL,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_results (
                    task_id TEXT PRIMARY KEY,
                    logic_score REAL,
                    factual_score REAL,
                    risk_score REAL,
                    alignment_score REAL,
                    passed INTEGER,
                    correction TEXT,
                    version_id TEXT,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS final_results (
                    task_id TEXT PRIMARY KEY,
                    content TEXT,
                    confidence REAL,
                    version_id TEXT,
                    trace TEXT,
                    verified_by TEXT,
                    audit_scores TEXT,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_versions_task 
                ON artifact_versions(task_id)
            """)
            conn.commit()

    async def commit(self, version: ArtifactVersion) -> str:
        """提交版本"""
        if not version.version_id:
            version.version_id = str(uuid4())[:8]

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._commit_sync, version)
        return version.version_id

    def _commit_sync(self, version: ArtifactVersion):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifact_versions 
                (version_id, task_id, agent_id, artifact_type, content, 
                 diff_from, score, status, snapshot, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    version.version_id,
                    version.task_id,
                    version.agent_id,
                    version.artifact_type.value,
                    version.content,
                    version.diff_from,
                    version.score,
                    version.status,
                    version.snapshot,
                    version.timestamp.isoformat(),
                    json.dumps(version.metadata),
                ),
            )
            conn.commit()

    async def rollback(self, version_id: str) -> Optional[ArtifactVersion]:
        """回滚到指定版本"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._rollback_sync, version_id)

    def _rollback_sync(self, version_id: str) -> Optional[ArtifactVersion]:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE artifact_versions SET status = 'rolled_back' WHERE version_id = ?",
                (version_id,),
            )
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
            if not row:
                return None

            return ArtifactVersion(
                version_id=row["version_id"],
                task_id=row["task_id"],
                agent_id=row["agent_id"],
                artifact_type=row["artifact_type"],
                content=row["content"],
                diff_from=row["diff_from"],
                score=row["score"],
                status="rolled_back",
                snapshot=row["snapshot"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                metadata=json.loads(row["metadata"] or "{}"),
            )

    async def snapshot(self, task_id: str, agent_id: str, action: str, result: Any):
        """保存快照"""
        version = ArtifactVersion(
            task_id=task_id,
            agent_id=agent_id,
            artifact_type="snapshot",
            content=str(result)[:1000] if result else "",
            status="pending",
            metadata={"action": action, "is_snapshot": True},
        )
        await self.commit(version)

    async def snapshot_stale(self, task_id: str, worker_id: str, reason: str):
        """保存呆死快照"""
        version = ArtifactVersion(
            task_id=task_id,
            agent_id=worker_id,
            artifact_type="stale_snapshot",
            content=f"Worker stale: {reason}",
            status="pending",
            metadata={"reason": reason, "is_stale_snapshot": True},
        )
        await self.commit(version)

    async def commit_audit(self, audit: AuditResult) -> None:
        """提交审核结果"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._commit_audit_sync, audit)

    def _commit_audit_sync(self, audit: AuditResult):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_results
                (task_id, logic_score, factual_score, risk_score, 
                 alignment_score, passed, correction, version_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    audit.task_id,
                    audit.logic_score,
                    audit.factual_score,
                    audit.risk_score,
                    audit.alignment_score,
                    1 if audit.passed else 0,
                    audit.correction,
                    audit.version_id,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()

    async def save_final_result(self, result: FinalResult) -> None:
        """保存最终结果"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._save_final_result_sync, result)

    def _save_final_result_sync(self, result: FinalResult):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO final_results
                (task_id, content, confidence, version_id, trace, 
                 verified_by, audit_scores, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    result.task_id,
                    result.content,
                    result.confidence,
                    result.version_id,
                    json.dumps(result.trace),
                    json.dumps(result.verified_by),
                    json.dumps(result.audit_scores),
                    result.created_at.isoformat(),
                ),
            )
            conn.commit()

    async def get_lineage(self, task_id: str) -> List[ArtifactVersion]:
        """获取版本血缘链"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_lineage_sync, task_id)

    def _get_lineage_sync(self, task_id: str) -> List[ArtifactVersion]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM artifact_versions 
                WHERE task_id = ? 
                ORDER BY timestamp ASC
            """,
                (task_id,),
            ).fetchall()

            return [
                ArtifactVersion(
                    version_id=row["version_id"],
                    task_id=row["task_id"],
                    agent_id=row["agent_id"],
                    artifact_type=row["artifact_type"],
                    content=row["content"],
                    diff_from=row["diff_from"],
                    score=row["score"],
                    status=row["status"],
                    snapshot=row["snapshot"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    metadata=json.loads(row["metadata"] or "{}"),
                )
                for row in rows
            ]

    async def get_versions(self, task_id: str) -> List[ArtifactVersion]:
        """获取任务的所有版本"""
        return await self.get_lineage(task_id)

    async def get_audit_result(self, task_id: str) -> Optional[AuditResult]:
        """获取审核结果"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_audit_result_sync, task_id)

    def _get_audit_result_sync(self, task_id: str) -> Optional[AuditResult]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM audit_results WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None

            return AuditResult(
                task_id=row["task_id"],
                logic_score=row["logic_score"],
                factual_score=row["factual_score"],
                risk_score=row["risk_score"],
                alignment_score=row["alignment_score"],
                passed=bool(row["passed"]),
                correction=row["correction"],
                version_id=row["version_id"],
            )

    async def get_final_result(self, task_id: str) -> Optional[FinalResult]:
        """获取最终结果"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_final_result_sync, task_id)

    def _get_final_result_sync(self, task_id: str) -> Optional[FinalResult]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM final_results WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None

            return FinalResult(
                task_id=row["task_id"],
                content=row["content"],
                confidence=row["confidence"],
                version_id=row["version_id"],
                trace=json.loads(row["trace"] or "{}"),
                created_at=datetime.fromisoformat(row["timestamp"]),
                verified_by=json.loads(row["verified_by"] or "[]"),
                audit_scores=json.loads(row["audit_scores"] or "{}"),
            )
