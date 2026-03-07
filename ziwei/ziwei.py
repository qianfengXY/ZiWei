#!/usr/bin/env python3
"""
ziwei.py
ZiWei CLI 主入口 - MVP 版本
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ziwei")


class ZiWeiRunner:
    """ZiWei 运行器"""

    def __init__(self):
        self.settings = self._load_settings()
        self.l0_adapter = None
        self.l1_executor = None
        self.version_store = None
        self.notifier = None

    def _load_settings(self):
        """加载配置"""
        from .config.settings import settings

        return settings

    async def initialize(self):
        """初始化组件"""
        logger.info("🔧 初始化 ZiWei 组件...")

        from .core.adapters import create_adapter
        from .core.enums import AgentRole
        from .infra.store import SQLiteStore
        from .infra.notifier import create_notifier

        l0_cfg = self.settings.l0_model
        self.l0_adapter = create_adapter(
            {
                "provider": l0_cfg.provider,
                "model": l0_cfg.model,
                "api_key": l0_cfg.api_key,
                "base_url": l0_cfg.base_url,
            },
            AgentRole.L0_BRAIN,
        )

        l1_cfg = self.settings.l1_executor_model
        self.l1_adapter = create_adapter(
            {
                "provider": l1_cfg.provider,
                "model": l1_cfg.model,
                "api_key": l1_cfg.api_key,
                "base_url": l1_cfg.base_url,
            },
            AgentRole.L1_EXECUTOR,
        )

        db_path = os.getenv("ZIWEI_DB_PATH", ".ziwei/ziwei.db")
        self.version_store = SQLiteStore(db_path)

        self.notifier = create_notifier(
            dingtalk_webhook=os.getenv("DINGTALK_WEBHOOK", ""),
            slack_webhook=os.getenv("SLACK_WEBHOOK", ""),
            block_on_urgent=True,
        )

        logger.info("✅ ZiWei 初始化完成")

    async def run_task(self, instruction: str) -> str:
        """执行任务"""
        from .core.models import Task, TaskComplexity
        from .agents.l0.brain import L0Brain
        from .agents.l1.executor import L1Executor
        from .agents.l1.validator import SimpleValidator
        from .agents.l1.tiebreaker import SimpleTiebreaker

        task = Task(
            instruction=instruction,
            complexity=TaskComplexity.MEDIUM,
        )

        l1_executor = L1Executor(
            instance_id="l1_executor_001",
            adapter=self.l1_adapter,
            worker_pool=DummyWorkerPool(),
            version_store=self.version_store,
            l0_client=DummyClient(),
            notifier=self.notifier,
            manifest_secret=self.settings.manifest_secret,
        )

        validator = SimpleValidator("validator_001")
        tiebreaker = SimpleTiebreaker("tiebreaker_001")

        l0_brain = L0Brain(
            adapter=self.l0_adapter,
            l1_executor=l1_executor,
            l1_validator=validator,
            l1_tiebreaker=tiebreaker,
            version_store=self.version_store,
            notifier=self.notifier,
            embedder=DummyEmbedder(),
        )

        logger.info(f"🚀 开始执行任务: {instruction[:50]}...")

        result = await l0_brain.run(task)

        return result

    async def run_interactive(self):
        """交互式模式"""
        print("\n" + "=" * 60)
        print("🪐 ZiWei 交互式对话模式")
        print("=" * 60)
        print("输入任务描述，按 Enter 提交")
        print("输入 'quit' 或 'exit' 退出")
        print("=" * 60 + "\n")

        await self.initialize()

        while True:
            try:
                instruction = input("\n👤 > ").strip()

                if not instruction:
                    continue
                if instruction.lower() in ["quit", "exit", "q"]:
                    print("👋 再见!")
                    break

                result = await self.run_task(instruction)
                print("\n" + "=" * 60)
                print("📤 ZiWei 回复:")
                print("=" * 60)
                print(result.content)
                print("=" * 60)
                print(f"📊 置信度: {result.confidence:.2%}")
                print(f"🆔 版本ID: {result.version_id}")
                print()

            except KeyboardInterrupt:
                print("\n👋 再见!")
                break
            except Exception as e:
                logger.error(f"执行出错: {e}", exc_info=True)
                print(f"❌ 错误: {e}")


class DummyWorkerPool:
    """虚拟 Worker 池 - MVP 用"""

    async def acquire(self, worker_type, manifest):
        return DummyWorker()

    async def release(self, worker):
        pass

    async def clear_worker(self, task_id):
        pass


class DummyWorker:
    """虚拟 Worker - MVP 用"""

    async def run(self, task):
        from .core.models import AgentResult
        from .core.enums import AgentRole

        return AgentResult(
            agent_id="dummy_worker",
            agent_role=AgentRole.L2_WORKER,
            task_id=task.id,
            content="[MVP] 模拟 Worker 执行结果",
            confidence=0.5,
            score=0.5,
            passed=True,
        )


class DummyClient:
    """虚拟 HTTP 客户端"""

    async def post(self, url, data):
        logger.info(f"[DummyClient] POST {url}")
        return {"approved": True, "token_id": "dummy_token"}

    async def get(self, url):
        return None


class DummyEmbedder:
    """虚拟 Embedder - MVP 用"""

    async def cosine_sim(self, text1: str, text2: str) -> float:
        return 0.8


async def main():
    parser = argparse.ArgumentParser(description="ZiWei - Multi-Agent 协同平台")
    parser.add_argument("instruction", nargs="?", help="任务指令")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互式模式")
    parser.add_argument(
        "--interactive", dest="interactive2", action="store_true", help="交互式模式"
    )

    args = parser.parse_args()

    runner = ZiWeiRunner()

    if args.internet or args.instruction:
        await runner.initialize()
        result = await runner.run_task(args.instruction)
        print("\n" + "=" * 60)
        print("📤 ZiWei 结果:")
        print("=" * 60)
        print(result.content)
        print("=" * 60)
        print(f"📊 置信度: {result.confidence:.2%}")
    else:
        await runner.run_interactive()


if __name__ == "__main__":
    asyncio.run(main())
