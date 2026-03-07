"""
ziwei/infra/notifier.py
基础通知器实现 - MVP 阶段使用控制台打印 + HTTP Webhook
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ziwei.notifier")


class BaseNotifier(ABC):
    """通知器基类"""

    @abstractmethod
    async def push_urgent(self, channels: List[str], payload: Dict[str, Any]) -> None:
        """紧急通知 - Mode A: 阻塞等待响应"""
        pass

    @abstractmethod
    async def push_warning(self, channels: List[str], payload: Dict[str, Any]) -> None:
        """警告推送 - 不阻塞"""
        pass

    @abstractmethod
    async def push_info(self, channels: List[str], payload: Dict[str, Any]) -> None:
        """信息推送 - Mode B"""
        pass

    @abstractmethod
    async def push_reminder(
        self, channels: List[str], task_id: str, elapsed_min: int
    ) -> None:
        """提醒推送"""
        pass

    @abstractmethod
    async def wait_approval(self, task_id: str, timeout_s: int) -> Optional[str]:
        """等待人工审批 - 返回 token 或 None"""
        pass


class ConsoleNotifier(BaseNotifier):
    """
    MVP 用控制台通知器
    支持：
    - 控制台打印
    - HTTP Webhook（可选）
    - 终端交互输入（人工决策）
    """

    def __init__(
        self,
        dingtalk_webhook: str = "",
        slack_webhook: str = "",
        block_on_urgent: bool = True,
    ):
        self.dingtalk_webhook = dingtalk_webhook or os.getenv("DINGTALK_WEBHOOK", "")
        self.slack_webhook = slack_webhook or os.getenv("SLACK_WEBHOOK", "")
        self.block_on_urgent = block_on_urgent

    async def push_urgent(self, channels: List[str], payload: Dict[str, Any]) -> None:
        """紧急通知"""
        message = self._format_message("🚨 URGENT", payload)

        if "console" in channels or not channels:
            print("\n" + "=" * 60)
            print("🚨 紧急通知 - 需要人工介入")
            print("=" * 60)
            print(message)
            print("=" * 60 + "\n")

        if "dingtalk" in channels and self.dingtalk_webhook:
            await self._send_webhook(self.dingtalk_webhook, payload)

        if "slack" in channels and self.slack_webhook:
            await self._send_webhook(self.slack_webhook, payload)

    async def push_warning(self, channels: List[str], payload: Dict[str, Any]) -> None:
        """警告推送"""
        message = self._format_message("⚠️ WARNING", payload)

        if "console" in channels or not channels:
            logger.warning(message)

        if "dingtalk" in channels and self.dingtalk_webhook:
            await self._send_webhook(self.dingtalk_webhook, payload)

        if "slack" in channels and self.slack_webhook:
            await self._send_webhook(self.slack_webhook, payload)

    async def push_info(self, channels: List[str], payload: Dict[str, Any]) -> None:
        """信息推送"""
        message = self._format_message("ℹ️ INFO", payload)

        if "console" in channels or not channels:
            logger.info(message)

        if "dingtalk" in channels and self.dingtalk_webhook:
            await self._send_webhook(self.dingtalk_webhook, payload)

    async def push_reminder(
        self, channels: List[str], task_id: str, elapsed_min: int
    ) -> None:
        """提醒推送"""
        payload = {
            "title": "任务超时提醒",
            "task_id": task_id,
            "message": f"任务 {task_id} 已等待 {elapsed_min} 分钟",
            "level": "warning",
        }
        await self.push_warning(channels, payload)

    async def wait_approval(self, task_id: str, timeout_s: int) -> Optional[str]:
        """
        等待人工审批
        MVP 阶段：从终端读取输入
        返回: token 如果批准, None 如果拒绝
        """
        if not self.block_on_urgent:
            logger.info(f"Mode B: 自动授权任务 {task_id}")
            return f"auto_approved_{task_id}"

        print("\n" + "=" * 60)
        print(f"⏳ 等待人工审批任务: {task_id}")
        print(f"⏱️ 超时时间: {timeout_s} 秒")
        print("=" * 60)

        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("批准此操作? (yes/no): ").strip().lower()
                ),
                timeout=timeout_s,
            )

            if response in ["yes", "y", "批准", "1"]:
                token = (
                    f"manual_approved_{task_id}_{int(asyncio.get_event_loop().time())}"
                )
                print(f"✅ 已批准! Token: {token}")
                return token
            else:
                print("❌ 已拒绝")
                return None

        except asyncio.TimeoutError:
            print(f"⏰ 审批超时，任务 {task_id} 将保存现场")
            return None
        except Exception as e:
            logger.error(f"等待审批时出错: {e}")
            return None

    def _format_message(self, level: str, payload: Dict[str, Any]) -> str:
        parts = [level]
        if title := payload.get("title"):
            parts.append(f"\n📌 {title}")
        if msg := payload.get("message"):
            parts.append(f"\n{msg}")
        if task_id := payload.get("task_id"):
            parts.append(f"\n🆔 Task: {task_id}")
        if worker_id := payload.get("worker_id"):
            parts.append(f"\n👷 Worker: {worker_id}")
        if action := payload.get("action"):
            parts.append(f"\n🔧 Action: {action}")
        if reason := payload.get("reason"):
            parts.append(f"\n📝 Reason: {reason}")

        return "".join(parts)

    async def _send_webhook(self, webhook_url: str, payload: Dict[str, Any]) -> bool:
        """发送 Webhook 通知"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                msg = {
                    "msgtype": "text",
                    "text": {"content": self._format_message("ZiWei", payload)},
                }
                await client.post(webhook_url, json=msg)
                return True
        except Exception as e:
            logger.warning(f"Webhook 发送失败: {e}")
            return False


def create_notifier(
    dingtalk_webhook: str = "",
    slack_webhook: str = "",
    block_on_urgent: bool = True,
) -> BaseNotifier:
    """工厂函数创建通知器"""
    return ConsoleNotifier(
        dingtalk_webhook=dingtalk_webhook,
        slack_webhook=slack_webhook,
        block_on_urgent=block_on_urgent,
    )
