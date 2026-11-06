"""试验任务抽取的提交、执行与人工确认。"""

from __future__ import annotations

import re
import uuid
from typing import Any

from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.test_tasks.extractor import MARKDOWN_LIMIT, extract_test_task_fields
from yuxi.knowledge.test_tasks.fields import (
    EXTRACTION_STATUS_LABELS,
    EXTRACTABLE_FILE_STATUSES,
    ChecklistError,
    apply_user_values,
    build_checklist,
    confirm_fields,
)
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.repositories.test_task_repository import TestTaskRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now
from yuxi.utils.logging_config import logger

_EXTRACTION_ID = re.compile(r"^tt_[a-f0-9]{16}$")
_MAX_FILES = 8
_ACTIVE_TASK_STATUSES = {"pending", "running"}

test_task_repository = TestTaskRepository()


class ExtractionError(Exception):
    """可返回给调用方的试验任务业务错误。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def create_extraction(kb_id: str, file_ids: list[str], operator_id: str) -> dict:
    """校验已解析文件后创建抽取记录，并在提交后投递 Durable Task。"""
    model_spec = await _require_llm_spec(kb_id)
    sources = await _load_sources(kb_id, file_ids)
    extraction_id = f"tt_{uuid.uuid4().hex[:16]}"
    await test_task_repository.create(
        {
            "extraction_id": extraction_id,
            "kb_id": kb_id,
            "status": "queued",
            "source_files": sources,
            "model_spec": model_spec,
            "checklist": [],
            "created_by": operator_id,
        }
    )
    try:
        task = await tasker.enqueue(
            name=f"试验任务抽取 ({sources[0]['filename']})",
            task_type="test_task_extract",
            payload={
                "extraction_id": extraction_id,
                "kb_id": kb_id,
                "file_ids": [item["file_id"] for item in sources],
                "files": sources,
                "model_spec": model_spec,
            },
        )
    except Exception as exc:
        logger.exception("提交试验任务抽取失败: extraction_id=%s", extraction_id)
        await test_task_repository.update_if_status(
            extraction_id,
            "queued",
            {"status": "failed", "error_message": f"提交抽取任务失败: {exc}"},
        )
        return await _serialize(await test_task_repository.get(extraction_id))
    await test_task_repository.update_if_status(
        extraction_id,
        "queued",
        {"durable_task_id": task.id},
    )
    return await _serialize(await test_task_repository.get(extraction_id), include_task_message=True)


async def list_extractions(kb_id: str) -> dict:
    """列出知识库下的试验任务抽取。"""
    records = await test_task_repository.list_by_kb(kb_id)
    return {"items": [await _serialize(record, include_checklist=False) for record in records]}


async def get_extraction(kb_id: str, extraction_id: str) -> dict:
    """读取一条抽取及其确认清单。"""
    record = await _require_record(kb_id, extraction_id)
    return await _serialize(record, include_task_message=True)


async def save_user_values(kb_id: str, extraction_id: str, values: dict) -> dict:
    """保存待确认清单上的人工编辑。"""
    record = await _require_record(kb_id, extraction_id)
    if record.status != "ready":
        raise ExtractionError(409, "当前状态不能编辑确认清单")
    try:
        checklist = apply_user_values(list(record.checklist or []), values)
    except ChecklistError as exc:
        raise ExtractionError(400, str(exc)) from exc
    updated = await test_task_repository.update_if_status(
        extraction_id,
        "ready",
        {"checklist": checklist},
    )
    if updated is None:
        raise ExtractionError(409, "确认清单已变化，请刷新后重试")
    return await _serialize(updated)


async def confirm_extraction(kb_id: str, extraction_id: str, operator_id: str, values: dict | None) -> dict:
    """校验并保存已确认试验任务。"""
    record = await _require_record(kb_id, extraction_id)
    if record.status != "ready":
        raise ExtractionError(409, "当前状态不能提交确认")
    checklist = list(record.checklist or [])
    try:
        if values is not None:
            checklist = apply_user_values(checklist, values)
        confirmed = confirm_fields(checklist)
    except ChecklistError as exc:
        raise ExtractionError(400, str(exc)) from exc
    updated = await test_task_repository.update_if_status(
        extraction_id,
        "ready",
        {
            "checklist": checklist,
            "confirmed_fields": confirmed,
            "status": "confirmed",
            "confirmed_by": operator_id,
            "confirmed_at": utc_now(),
            "error_message": None,
        },
    )
    if updated is None:
        raise ExtractionError(409, "确认清单已变化，请刷新后重试")
    return await _serialize(updated)


async def retry_extraction(kb_id: str, extraction_id: str) -> dict:
    """对失败的抽取重新投递任务。进行中的任务保持原记录。"""
    record = await _require_record(kb_id, extraction_id)
    if record.status in {"running", "confirmed"}:
        raise ExtractionError(409, "当前状态不能重新抽取")
    if record.status == "ready":
        raise ExtractionError(409, "待确认清单请直接校核；如需重抽，请从文件再次发起")
    if record.status == "queued" and await _task_is_active(record.durable_task_id):
        return await _serialize(record, include_task_message=True)
    model_spec = await _require_llm_spec(kb_id)
    queued = await test_task_repository.update_if_status(
        extraction_id,
        record.status,
        {
            "status": "queued",
            "durable_task_id": None,
            "error_message": None,
            "checklist": [],
            "model_spec": model_spec,
        },
    )
    if queued is None:
        raise ExtractionError(409, "抽取状态已变化，请刷新后重试")
    try:
        task = await tasker.enqueue(
            name=f"试验任务抽取 ({_display_name(queued)})",
            task_type="test_task_extract",
            payload={
                "extraction_id": queued.extraction_id,
                "kb_id": kb_id,
                "file_ids": [item["file_id"] for item in (queued.source_files or [])],
                "files": list(queued.source_files or []),
                "model_spec": model_spec,
            },
        )
    except Exception as exc:
        logger.exception("重新提交试验任务抽取失败: extraction_id=%s", extraction_id)
        await test_task_repository.update_if_status(
            extraction_id,
            "queued",
            {"status": "failed", "error_message": f"提交抽取任务失败: {exc}"},
        )
        return await _serialize(await test_task_repository.get(extraction_id))
    await test_task_repository.update_if_status(extraction_id, "queued", {"durable_task_id": task.id})
    return await _serialize(await test_task_repository.get(extraction_id), include_task_message=True)


async def run_test_task_extract(context: TaskContext) -> dict:
    """从持久 payload 读取 Markdown，调用模型并生成确认清单。"""
    payload = context.payload
    extraction_id = payload["extraction_id"]
    if not await test_task_repository.mark_running(extraction_id, context.task_id):
        raise ValueError("试验任务不存在或已不再由该任务执行")
    await context.set_progress(15, "读取解析后的 Markdown")
    documents = await _load_documents(payload["kb_id"], list(payload.get("files") or []))
    await context.raise_if_cancelled()
    await context.set_progress(45, "调用模型抽取试验任务字段")
    raw = await extract_test_task_fields(payload["model_spec"], documents)
    checklist = build_checklist(raw, list(payload.get("file_ids") or []))
    await context.set_progress(100, "确认清单已生成")
    return {"extraction_id": extraction_id, "checklist": checklist}


async def finish_test_task_extract(session, task_record, result) -> None:
    """在 Task 成功事务内写入待确认清单。"""
    extraction_id = (task_record.payload or {}).get("extraction_id")
    if not isinstance(result, dict) or result.get("extraction_id") != extraction_id:
        raise ValueError("试验任务抽取结果与任务不匹配")
    checklist = result.get("checklist")
    if not isinstance(checklist, list):
        raise ValueError("试验任务抽取结果缺少确认清单")
    record = await TestTaskRepository.get_for_update(session, extraction_id)
    if record is None:
        raise ValueError("试验任务不存在")
    if record.status == "confirmed" or record.durable_task_id not in {None, task_record.id}:
        return
    record.durable_task_id = task_record.id
    record.checklist = checklist
    record.status = "ready"
    record.error_message = None
    record.updated_at = utc_now()


async def fail_test_task_extract(session, task_record, error: str) -> None:
    """在 Task 失败事务内记录可见错误。已确认结果不被失败覆盖。"""
    extraction_id = (task_record.payload or {}).get("extraction_id")
    record = await TestTaskRepository.get_for_update(session, extraction_id)
    if record is None or record.status == "confirmed":
        return
    if record.durable_task_id not in {None, task_record.id}:
        return
    record.durable_task_id = record.durable_task_id or task_record.id
    record.status = "failed"
    record.error_message = (error or "抽取失败")[:2000]
    record.updated_at = utc_now()


async def _require_llm_spec(kb_id: str) -> str:
    db_info, supports_documents = await knowledge_base.get_database_document_support(kb_id)
    if db_info is None:
        raise ExtractionError(404, "知识库不存在")
    if not supports_documents:
        raise ExtractionError(400, "当前知识库不支持文档解析")
    model_spec = (db_info.llm_model_spec or "").strip()
    if not model_spec:
        raise ExtractionError(400, "知识库未配置 LLM 模型")
    return model_spec


async def _load_sources(kb_id: str, file_ids: list[str]) -> list[dict[str, str]]:
    normalized = []
    for file_id in file_ids:
        if not isinstance(file_id, str):
            continue
        value = file_id.strip()
        if not value or value in normalized:
            continue
        if len(value) > 64 or any(char.isspace() for char in value):
            raise ExtractionError(400, "文件标识不合法")
        normalized.append(value)
    if not normalized:
        raise ExtractionError(400, "请选择至少一个已解析文件")
    if len(normalized) > _MAX_FILES:
        raise ExtractionError(400, "单次最多选择 8 个文件")
    records = await KnowledgeFileRepository().list_by_file_ids(normalized)
    by_id = {record.file_id: record for record in records}
    sources = []
    for file_id in normalized:
        record = by_id.get(file_id)
        if record is None or record.kb_id != kb_id:
            raise ExtractionError(404, "部分文件不存在")
        filename = record.original_filename or record.filename or file_id
        if record.is_folder or record.status not in EXTRACTABLE_FILE_STATUSES or not record.markdown_file:
            raise ExtractionError(400, f"{filename} 尚未完成解析")
        sources.append({"file_id": file_id, "filename": filename})
    return sources


async def _load_documents(kb_id: str, files: list[dict]) -> list[dict]:
    documents = []
    for item in files:
        file_id = item["file_id"]
        filename = item.get("filename") or file_id
        content = await knowledge_base.get_file_content(kb_id, file_id)
        markdown = content.get("content") if isinstance(content, dict) else None
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError(f"{filename} 没有可用的 Markdown")
        documents.append(
            {
                "file_id": file_id,
                "filename": filename,
                "markdown": markdown.strip()[:MARKDOWN_LIMIT],
            }
        )
    if not documents:
        raise ValueError("没有可抽取的文档")
    return documents


async def _require_record(kb_id: str, extraction_id: str):
    if not _EXTRACTION_ID.fullmatch(extraction_id or ""):
        raise ExtractionError(404, "试验任务不存在")
    record = await test_task_repository.get(extraction_id)
    if record is None or record.kb_id != kb_id:
        raise ExtractionError(404, "试验任务不存在")
    return record


async def _task_is_active(task_id: str | None) -> bool:
    if not task_id:
        return False
    task = await TaskRepository().get_by_id(task_id)
    return task is not None and task.status in _ACTIVE_TASK_STATUSES


async def _serialize(
    record,
    *,
    include_checklist: bool = True,
    include_task_message: bool = False,
) -> dict[str, Any]:
    if record is None:
        raise ExtractionError(404, "试验任务不存在")
    checklist = list(record.checklist or [])
    payload = {
        "extraction_id": record.extraction_id,
        "kb_id": record.kb_id,
        "status": record.status,
        "status_label": EXTRACTION_STATUS_LABELS.get(record.status, record.status),
        "display_name": _display_name(record),
        "source_files": list(record.source_files or []),
        "model_spec": record.model_spec,
        "durable_task_id": record.durable_task_id,
        "error_message": record.error_message,
        "issue_count": sum(1 for item in checklist if item.get("status") != "extracted"),
        "confirmed_fields": record.confirmed_fields,
        "created_at": format_utc_datetime(record.created_at),
        "updated_at": format_utc_datetime(record.updated_at),
        "confirmed_at": format_utc_datetime(record.confirmed_at),
    }
    if include_checklist:
        payload["checklist"] = checklist
    if include_task_message:
        payload["task_message"] = await _task_message(record)
    return payload


async def _task_message(record) -> str | None:
    if record.status not in {"queued", "running"} or not record.durable_task_id:
        return None
    task = await TaskRepository().get_by_id(record.durable_task_id)
    if task is None:
        return None
    return task.message or None


def _display_name(record) -> str:
    confirmed = record.confirmed_fields or {}
    if isinstance(confirmed, dict) and confirmed.get("task_name"):
        return str(confirmed["task_name"])
    for item in record.checklist or []:
        if item.get("field_key") != "task_name":
            continue
        value = item.get("user_value")
        if value is None:
            value = item.get("extracted_value")
        if isinstance(value, list):
            value = "；".join(str(part) for part in value if part)
        if value:
            return str(value)
    files = record.source_files or []
    if files and files[0].get("filename"):
        return str(files[0]["filename"])
    return "未命名试验任务"
