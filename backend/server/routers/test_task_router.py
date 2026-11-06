"""试验任务抽取与确认清单的 HTTP 边界。"""

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from yuxi.services.test_task_service import ExtractionError, confirm_extraction, create_extraction
from yuxi.services.test_task_service import get_extraction as get_test_task_extraction
from yuxi.services.test_task_service import list_extractions, retry_extraction, save_user_values
from yuxi.storage.postgres.models_business import User

from server.utils.knowledge_permissions import require_knowledge_base_manage, require_knowledge_base_read

test_tasks = APIRouter(prefix="/knowledge", tags=["knowledge"])


class CreateTestTaskRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=8)


class TestTaskValuesRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


def _raise_test_task_error(error: ExtractionError) -> NoReturn:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@test_tasks.get("/databases/{kb_id}/test-tasks")
async def list_test_tasks(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """列出当前知识库的试验任务抽取。"""
    try:
        return await list_extractions(kb_id)
    except ExtractionError as error:
        _raise_test_task_error(error)


@test_tasks.post("/databases/{kb_id}/test-tasks")
async def create_test_task(
    kb_id: str,
    payload: CreateTestTaskRequest,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """对已解析文档发起试验任务结构化抽取。"""
    try:
        return await create_extraction(kb_id, payload.file_ids, current_user.uid)
    except ExtractionError as error:
        _raise_test_task_error(error)


@test_tasks.get("/databases/{kb_id}/test-tasks/{extraction_id}")
async def get_test_task(
    kb_id: str,
    extraction_id: str,
    current_user: User = Depends(require_knowledge_base_read),
):
    """读取确认清单。"""
    try:
        return await get_test_task_extraction(kb_id, extraction_id)
    except ExtractionError as error:
        _raise_test_task_error(error)


@test_tasks.put("/databases/{kb_id}/test-tasks/{extraction_id}/fields")
async def update_test_task_fields(
    kb_id: str,
    extraction_id: str,
    payload: TestTaskValuesRequest,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """保存确认清单中的人工编辑。"""
    try:
        return await save_user_values(kb_id, extraction_id, payload.values)
    except ExtractionError as error:
        _raise_test_task_error(error)


@test_tasks.post("/databases/{kb_id}/test-tasks/{extraction_id}/confirm")
async def confirm_test_task(
    kb_id: str,
    extraction_id: str,
    payload: TestTaskValuesRequest | None = None,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """提交人工确认并持久化试验任务。"""
    try:
        values = None if payload is None else payload.values
        return await confirm_extraction(kb_id, extraction_id, current_user.uid, values)
    except ExtractionError as error:
        _raise_test_task_error(error)


@test_tasks.post("/databases/{kb_id}/test-tasks/{extraction_id}/retry")
async def retry_test_task(
    kb_id: str,
    extraction_id: str,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """重新发起失败的结构化抽取。"""
    try:
        return await retry_extraction(kb_id, extraction_id)
    except ExtractionError as error:
        _raise_test_task_error(error)
