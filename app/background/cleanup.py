"""后台任务：定期清理过期的 PDF 文件。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.config import Settings
from app.config import settings as default_settings
from app.services import storage

logger = logging.getLogger(__name__)


async def cleanup_loop(
    stop_event: asyncio.Event,
    *,
    cfg: Settings = default_settings,
    run_once_at_startup: bool = True,
) -> None:
    """每隔 cleanup_interval_minutes 清理一次；stop_event 置位后退出。"""
    interval = max(cfg.cleanup_interval_minutes, 1) * 60

    if run_once_at_startup:
        _safe_cleanup(cfg)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break  # 收到停止信号
        except asyncio.TimeoutError:
            _safe_cleanup(cfg)

    logger.info("过期清理任务已退出")


def _safe_cleanup(cfg: Settings) -> None:
    try:
        storage.cleanup_expired(cfg)
        storage.cleanup_uploads(cfg)
    except Exception:  # 清理失败绝不能影响主流程
        logger.exception("过期清理任务异常")


def start_cleanup_task(cfg: Settings = default_settings) -> tuple[asyncio.Task, asyncio.Event]:
    """启动清理任务，返回 (task, stop_event)。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(cleanup_loop(stop_event, cfg=cfg), name="pdf-cleanup")
    logger.info("过期清理任务已启动，间隔 %d 分钟，TTL %d 小时", cfg.cleanup_interval_minutes, cfg.pdf_ttl_hours)
    return task, stop_event


def stop_cleanup_task(task: Optional[asyncio.Task], stop_event: Optional[asyncio.Event]) -> None:
    if stop_event is not None:
        stop_event.set()
    if task is not None and not task.done():
        task.cancel()
