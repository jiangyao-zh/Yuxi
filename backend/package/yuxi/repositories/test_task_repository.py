"""试验任务抽取记录的 PostgreSQL 访问。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import TestTaskExtraction
from yuxi.utils.datetime_utils import utc_now


class TestTaskRepository:
    async def create(self, data: dict[str, Any]) -> TestTaskExtraction:
        """创建一条抽取记录。"""
        async with pg_manager.get_async_session_context() as session:
            record = TestTaskExtraction(**data)
            session.add(record)
            await session.flush()
            return record

    async def get(self, extraction_id: str) -> TestTaskExtraction | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(TestTaskExtraction).where(TestTaskExtraction.extraction_id == extraction_id)
            )

    async def list_by_kb(self, kb_id: str, limit: int = 100) -> list[TestTaskExtraction]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(TestTaskExtraction)
                .where(TestTaskExtraction.kb_id == kb_id)
                .order_by(TestTaskExtraction.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def update_if_status(
        self,
        extraction_id: str,
        expected_status: str,
        data: dict[str, Any],
    ) -> TestTaskExtraction | None:
        """仅当记录仍处于期望状态时写入，避免并发确认覆盖终态。"""
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(TestTaskExtraction).where(TestTaskExtraction.extraction_id == extraction_id).with_for_update()
            )
            if record is None or record.status != expected_status:
                return None
            for key, value in data.items():
                setattr(record, key, value)
            record.updated_at = utc_now()
            await session.flush()
            return record

    async def mark_running(self, extraction_id: str, task_id: str) -> bool:
        """把仍由该 Durable Task 排队的记录标成抽取中。"""
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(TestTaskExtraction).where(TestTaskExtraction.extraction_id == extraction_id).with_for_update()
            )
            if record is None or record.status != "queued" or record.durable_task_id not in {None, task_id}:
                return False
            record.durable_task_id = task_id
            record.status = "running"
            record.updated_at = utc_now()
            await session.flush()
            return True

    @staticmethod
    async def get_for_update(session, extraction_id: str) -> TestTaskExtraction | None:
        """在调用方事务中锁定抽取记录。"""
        return await session.scalar(
            select(TestTaskExtraction).where(TestTaskExtraction.extraction_id == extraction_id).with_for_update()
        )
