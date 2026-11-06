"""试验任务确认清单的规则与抽取作业边界。"""

from types import SimpleNamespace

import pytest

from yuxi.knowledge.graphs.extractors.factory import GraphExtractorFactory
from yuxi.knowledge.test_tasks.extractor import build_extraction_prompt, parse_model_json
from yuxi.knowledge.test_tasks.fields import ChecklistError, apply_user_values, build_checklist, confirm_fields
from yuxi.services import test_task_service
from yuxi.services.task_registry import get_task_definition
from yuxi.services.test_task_service import ExtractionError, create_extraction, run_test_task_extract
from yuxi.services.test_task_service import fail_test_task_extract, finish_test_task_extract


def _payload(**fields):
    return {"fields": fields, "files": [], "file_ids": ["file-1"]}


def test_missing_required_field_blocks_confirmation():
    checklist = build_checklist(_payload(), ["file-1"])
    task_name = next(item for item in checklist if item["field_key"] == "task_name")
    assert task_name["status"] == "missing"
    assert task_name["status_label"] == "缺失"

    with pytest.raises(ChecklistError, match="试验任务名称"):
        confirm_fields(checklist)


def test_user_can_fill_missing_required_field_and_confirm():
    checklist = build_checklist(
        {
            "fields": {
                "test_purpose": {"value": "考核热稳定性", "confidence": 0.9, "excerpt": "考核热稳定性"},
                "dut": {"value": "控制盒", "confidence": 0.9, "excerpt": "控制盒"},
                "assessment_metrics": {"value": ["温度不超过 85℃"], "confidence": 0.9, "excerpt": "85℃"},
            },
            "files": [{"file_id": "file-1", "dut": "控制盒", "assessment_metrics": ["温度不超过 85℃"]}],
        },
        ["file-1"],
    )
    with pytest.raises(ChecklistError, match="试验任务名称"):
        confirm_fields(checklist)

    checklist = apply_user_values(checklist, {"task_name": "高温贮存试验"})
    confirmed = confirm_fields(checklist)
    assert confirmed["task_name"] == "高温贮存试验"
    assert confirmed["assessment_metrics"] == ["温度不超过 85℃"]
    assert confirmed["task_code"] == ""


def test_low_confidence_conflict_illegal_code_and_date_are_flagged():
    checklist = build_checklist(
        {
            "fields": {
                "task_name": {"value": "振动试验", "confidence": 0.2, "excerpt": "振动试验", "uncertain": True},
                "task_code": {"value": "任务 01", "confidence": 0.9, "excerpt": "任务 01"},
                "test_purpose": {"value": "考核结构", "confidence": 0.95, "excerpt": "考核结构"},
                "dut": {"value": "控制器甲", "confidence": 0.9, "excerpt": "控制器甲"},
                "assessment_metrics": {"value": ["加速度 12 xyz"], "confidence": 0.9, "excerpt": "12 xyz"},
                "schedule": {"value": "2026年13月2日", "confidence": 0.9, "excerpt": "2026年13月2日"},
            },
            "files": [
                {"file_id": "file-1", "dut": "控制器甲", "assessment_metrics": ["加速度 12 xyz"]},
                {"file_id": "file-2", "dut": "控制器乙", "assessment_metrics": ["加速度 12 xyz"]},
            ],
        },
        ["file-1", "file-2"],
    )
    by_key = {item["field_key"]: item for item in checklist}
    assert by_key["task_name"]["status"] == "uncertain"
    assert by_key["task_code"]["status"] == "invalid_format"
    assert by_key["schedule"]["status"] == "invalid_format"
    assert by_key["assessment_metrics"]["status"] == "invalid_format"
    assert by_key["dut"]["status"] == "conflict"
    assert by_key["dut"]["extracted_value"] == ["控制器甲", "控制器乙"]

    with pytest.raises(ChecklistError, match="被试品需要人工确认"):
        confirm_fields(checklist)

    corrected = apply_user_values(
        checklist,
        {
            "task_name": "振动试验",
            "task_code": "RW-2026-01",
            "dut": "控制器甲",
            "assessment_metrics": ["加速度 12 g"],
            "schedule": "2026-06-01",
        },
    )
    confirmed = confirm_fields(corrected)
    assert confirmed["dut"] == "控制器甲"
    assert confirmed["task_code"] == "RW-2026-01"
    assert confirmed["assessment_metrics"] == ["加速度 12 g"]


def test_common_codes_and_units_remain_extracted_until_a_metric_conflict_is_resolved():
    checklist = build_checklist(
        {
            "fields": {
                "task_name": {"value": "绝缘试验", "confidence": 0.95, "excerpt": "绝缘试验"},
                "task_code": {"value": "试验-2024-01", "confidence": 0.95, "excerpt": "试验-2024-01"},
                "test_purpose": {"value": "考核绝缘", "confidence": 0.95, "excerpt": "考核绝缘"},
                "dut": {"value": "电缆", "confidence": 0.95, "excerpt": "电缆"},
                "assessment_metrics": {
                    "value": ["绝缘电阻 10kΩ", "湿度 95%RH", "漏电流 5µA", "转角 90°"],
                    "confidence": 0.95,
                    "excerpt": "10kΩ",
                },
            },
            "files": [
                {
                    "file_id": "file-1",
                    "dut": "电缆",
                    "assessment_metrics": ["绝缘电阻 10kΩ", "湿度 95%RH", "漏电流 5µA", "转角 90°"],
                }
            ],
        },
        ["file-1"],
    )
    by_key = {item["field_key"]: item for item in checklist}
    assert by_key["task_code"]["status"] == "extracted"
    assert by_key["assessment_metrics"]["status"] == "extracted"
    assert (
        next(
            item["status"]
            for item in build_checklist(
                {
                    "fields": {"task_code": {"value": "任务 01", "confidence": 0.9, "excerpt": "任务 01"}},
                    "files": [],
                },
                ["file-1"],
            )
            if item["field_key"] == "task_code"
        )
        == "invalid_format"
    )

    conflicted = build_checklist(
        {
            "fields": {
                "task_name": {"value": "振动试验", "confidence": 0.95, "excerpt": "振动试验"},
                "test_purpose": {"value": "考核结构", "confidence": 0.95, "excerpt": "考核结构"},
                "dut": {"value": "控制器甲", "confidence": 0.95, "excerpt": "控制器甲"},
                "assessment_metrics": {"value": ["加速度 5g"], "confidence": 0.95, "excerpt": "5g"},
            },
            "files": [
                {"file_id": "file-1", "dut": "控制器甲", "assessment_metrics": ["加速度 5g"]},
                {"file_id": "file-2", "dut": "控制器甲", "assessment_metrics": ["加速度 12g"]},
            ],
        },
        ["file-1", "file-2"],
    )
    partial = apply_user_values(conflicted, {"dut": "控制器甲"})
    with pytest.raises(ChecklistError, match="考核指标需要人工确认"):
        confirm_fields(partial)


def test_model_json_must_be_an_object_and_prompt_names_schema():
    assert parse_model_json('说明\n```json\n{"fields": {}}\n```') == {"fields": {}}
    with pytest.raises(ValueError, match="不是 JSON 对象"):
        parse_model_json("没有结构化结果")
    prompt = build_extraction_prompt(
        [{"file_id": "file-1", "filename": "任务书.md", "markdown": "试验任务名称：高温试验"}]
    )
    assert "task_name" in prompt
    assert "assessment_metrics" in prompt
    assert "file-1" in prompt


def test_graph_extractor_registry_does_not_accept_test_task_type():
    assert "llm" in GraphExtractorFactory.supported_types()
    assert "test_task" not in GraphExtractorFactory.supported_types()
    with pytest.raises(ValueError, match="不支持的图谱抽取器类型"):
        GraphExtractorFactory.create("test_task")
    assert get_task_definition("knowledge_graph_index").function == "run_knowledge_graph"
    definition = get_task_definition("test_task_extract")
    assert definition.failure_function == "fail_test_task_extract"
    assert definition.module == "yuxi.services.test_task_service"


class _Session:
    def __init__(self, record):
        self.record = record

    async def scalar(self, _statement):
        return self.record


@pytest.mark.asyncio
async def test_success_hook_writes_checklist_and_failure_cannot_overwrite_confirmation():
    record = SimpleNamespace(
        status="running",
        durable_task_id="task-1",
        checklist=[],
        error_message="旧错误",
        updated_at=None,
    )
    task = SimpleNamespace(id="task-1", payload={"extraction_id": "tt_0123456789abcdef"})
    checklist = [{"field_key": "task_name", "status": "extracted"}]
    await finish_test_task_extract(
        _Session(record),
        task,
        {"extraction_id": "tt_0123456789abcdef", "checklist": checklist},
    )
    assert record.status == "ready"
    assert record.checklist == checklist
    assert record.error_message is None

    record.status = "confirmed"
    await fail_test_task_extract(_Session(record), task, "worker lost")
    assert record.status == "confirmed"

    record.status = "running"
    record.durable_task_id = "newer-task"
    await fail_test_task_extract(_Session(record), task, "迟到失败")
    assert record.status == "running"
    assert record.error_message is None


@pytest.mark.asyncio
async def test_extract_handler_keeps_anomalies_and_surfaces_model_errors(monkeypatch):
    async def mark_running(_extraction_id, _task_id):
        return True

    async def read_markdown(_kb_id, file_id):
        return {"content": f"{file_id} 的解析正文"}

    async def extract(_model_spec, documents):
        assert len(documents) == 2
        return {
            "fields": {
                "task_name": {"value": "淋雨试验", "confidence": 0.93, "excerpt": "淋雨试验"},
                "test_purpose": {"value": "考核密封", "confidence": 0.91, "excerpt": "考核密封"},
                "dut": {"value": "舱段", "confidence": 0.9, "excerpt": "舱段"},
                "assessment_metrics": {"value": ["淋雨 30 min"], "confidence": 0.92, "excerpt": "30 min"},
            },
            "files": [
                {"file_id": "file-1", "dut": "舱段甲", "assessment_metrics": ["淋雨 30 min"]},
                {"file_id": "file-2", "dut": "舱段乙", "assessment_metrics": ["淋雨 30 min"]},
            ],
        }

    monkeypatch.setattr(test_task_service.test_task_repository, "mark_running", mark_running)
    monkeypatch.setattr(test_task_service.knowledge_base, "get_file_content", read_markdown)
    monkeypatch.setattr(test_task_service, "extract_test_task_fields", extract)
    context = SimpleNamespace(
        task_id="task-1",
        payload={
            "extraction_id": "tt_0123456789abcdef",
            "kb_id": "kb-1",
            "model_spec": "chat/demo",
            "file_ids": ["file-1", "file-2"],
            "files": [
                {"file_id": "file-1", "filename": "任务书.md"},
                {"file_id": "file-2", "filename": "年度计划.md"},
            ],
        },
    )

    async def set_progress(_progress, _message=None):
        return None

    async def raise_if_cancelled():
        return None

    context.set_progress = set_progress
    context.raise_if_cancelled = raise_if_cancelled
    result = await run_test_task_extract(context)
    dut = next(item for item in result["checklist"] if item["field_key"] == "dut")
    assert dut["status"] == "conflict"

    async def broken_extract(_model_spec, _documents):
        raise ValueError("模型返回的 JSON 无法解析")

    monkeypatch.setattr(test_task_service, "extract_test_task_fields", broken_extract)
    with pytest.raises(ValueError, match="JSON 无法解析"):
        await run_test_task_extract(context)


@pytest.mark.asyncio
async def test_create_rejects_missing_model_and_unparsed_file(monkeypatch):
    database = SimpleNamespace(llm_model_spec="")

    async def support(_kb_id):
        return database, True

    async def enqueue(**_kwargs):
        raise AssertionError("未通过校验时不能投递抽取任务")

    monkeypatch.setattr(test_task_service.knowledge_base, "get_database_document_support", support)
    monkeypatch.setattr(test_task_service.tasker, "enqueue", enqueue)

    with pytest.raises(ExtractionError, match="LLM") as missing_model:
        await create_extraction("kb-1", ["file-1"], "user-1")
    assert missing_model.value.status_code == 400

    database.llm_model_spec = "chat/demo"

    class File:
        file_id = "file-1"
        kb_id = "kb-1"
        is_folder = False
        status = "uploaded"
        markdown_file = None
        original_filename = "任务书.pdf"
        filename = "任务书.pdf"

    class Repo:
        async def list_by_file_ids(self, _file_ids):
            return [File()]

    monkeypatch.setattr(test_task_service, "KnowledgeFileRepository", lambda: Repo())
    with pytest.raises(ExtractionError, match="尚未完成解析"):
        await create_extraction("kb-1", ["file-1"], "user-1")


@pytest.mark.asyncio
async def test_enqueue_failure_is_stored_on_the_extraction(monkeypatch):
    database = SimpleNamespace(llm_model_spec="chat/demo")

    async def support(_kb_id):
        return database, True

    class File:
        file_id = "file-1"
        kb_id = "kb-1"
        is_folder = False
        status = "parsed"
        markdown_file = "kb/file.md"
        original_filename = "任务书.pdf"
        filename = "任务书.pdf"

    class Files:
        async def list_by_file_ids(self, _file_ids):
            return [File()]

    class Records:
        def __init__(self):
            self.record = None

        async def create(self, data):
            self.record = SimpleNamespace(
                durable_task_id=None,
                error_message=None,
                confirmed_fields=None,
                confirmed_at=None,
                created_at=None,
                updated_at=None,
                **data,
            )
            return self.record

        async def update_if_status(self, _extraction_id, expected_status, data):
            if self.record.status != expected_status:
                return None
            for key, value in data.items():
                setattr(self.record, key, value)
            return self.record

        async def get(self, _extraction_id):
            return self.record

    async def enqueue(**_kwargs):
        raise RuntimeError("队列不可用")

    records = Records()
    monkeypatch.setattr(test_task_service.knowledge_base, "get_database_document_support", support)
    monkeypatch.setattr(test_task_service, "KnowledgeFileRepository", lambda: Files())
    monkeypatch.setattr(test_task_service, "test_task_repository", records)
    monkeypatch.setattr(test_task_service.tasker, "enqueue", enqueue)

    result = await create_extraction("kb-1", ["file-1"], "user-1")
    assert result["status"] == "failed"
    assert "队列不可用" in result["error_message"]
