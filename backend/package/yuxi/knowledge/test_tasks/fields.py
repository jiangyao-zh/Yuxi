"""试验任务字段、异常判定与确认清单。"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

CONFIDENCE_UNCERTAIN_BELOW = 0.6
EXCERPT_LIMIT = 400
USER_VALUE_LIMIT = 2000
USER_LIST_LIMIT = 30
USER_ITEM_LIMIT = 300

FIELD_STATUS_LABELS = {
    "extracted": "已抽取",
    "missing": "缺失",
    "uncertain": "存疑",
    "conflict": "冲突",
    "invalid_format": "格式异常",
}
EXTRACTION_STATUS_LABELS = {
    "queued": "排队中",
    "running": "抽取中",
    "ready": "待确认",
    "confirmed": "已确认",
    "failed": "抽取失败",
}
EXTRACTABLE_FILE_STATUSES = {"parsed", "indexed", "done", "error_indexing"}

_TASK_CODE = re.compile(r"^[\w][\w./-]{0,63}$", re.UNICODE)
_FULL_DATE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*月")
_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*日")
_METRIC_UNIT = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*([A-Za-zµμ°%Ω℃]{1,16})")
_ALLOWED_UNITS = {
    "%",
    "c",
    "f",
    "k",
    "mm",
    "cm",
    "m",
    "km",
    "um",
    "nm",
    "g",
    "kg",
    "t",
    "pa",
    "kpa",
    "mpa",
    "gpa",
    "v",
    "mv",
    "kv",
    "a",
    "ma",
    "hz",
    "khz",
    "mhz",
    "ghz",
    "s",
    "ms",
    "min",
    "h",
    "d",
    "n",
    "kn",
    "w",
    "kw",
    "rpm",
    "db",
    "mol",
    "l",
    "ml",
    "ohm",
    "kohm",
    "mohm",
    "gohm",
    "ua",
    "deg",
    "%rh",
    "%fs",
    "px",
}


@dataclass(frozen=True)
class FieldSpec:
    """确认清单中的一个试验任务字段。"""

    key: str
    label: str
    required: bool
    multiple: bool


FIELD_SPECS = (
    FieldSpec("task_name", "试验任务名称", True, False),
    FieldSpec("task_code", "任务编号", False, False),
    FieldSpec("test_purpose", "试验目的", True, False),
    FieldSpec("dut", "被试品", True, False),
    FieldSpec("dut_model", "被试品型号/规格", False, False),
    FieldSpec("assessment_metrics", "考核指标", True, True),
    FieldSpec("test_items", "试验项目/科目", False, True),
    FieldSpec("test_conditions", "试验条件/环境", False, True),
    FieldSpec("standards", "依据标准/规范", False, True),
    FieldSpec("schedule", "计划节点/年度安排", False, False),
    FieldSpec("responsible_org", "责任单位/科室", False, False),
    FieldSpec("attachments_ref", "关联附件类型", False, True),
)
FIELD_BY_KEY = {spec.key: spec for spec in FIELD_SPECS}


class ChecklistError(ValueError):
    """确认清单不满足提交条件。"""


def build_checklist(payload: dict, file_ids: list[str]) -> list[dict]:
    """把模型 JSON 收成固定字段清单，并标出缺失、存疑、格式异常和跨附件冲突。"""
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    conflicts = _cross_file_conflicts(payload.get("files"), file_ids)
    items = []
    for spec in FIELD_SPECS:
        raw = fields.get(spec.key) if isinstance(fields.get(spec.key), dict) else {}
        value = _coerce(raw.get("value"), spec.multiple)
        status, note = _classify(spec, value, _confidence(raw.get("confidence")), bool(raw.get("uncertain")))
        excerpt = _excerpt(raw.get("excerpt"))
        source_file_id = _source_file_id(raw.get("file_id"), file_ids)
        conflict = conflicts.get(spec.key)
        if conflict is not None:
            value = conflict["values"]
            status = "conflict"
            note = conflict["note"]
            if spec.key == "assessment_metrics" and _metrics_have_illegal_unit(value):
                note = f"{note}，且存在无法识别的单位"
            source_file_id = None
            if not excerpt:
                excerpt = "；".join(str(item) for item in value)
        items.append(
            {
                "field_key": spec.key,
                "label": spec.label,
                "required": spec.required,
                "multiple": spec.multiple,
                "extracted_value": value,
                "source_excerpt": excerpt[:EXCERPT_LIMIT],
                "source_file_id": source_file_id,
                "status": status,
                "status_label": FIELD_STATUS_LABELS[status],
                "note": note,
                "user_value": None,
            }
        )
    return items


def apply_user_values(checklist: list[dict], values: dict) -> list[dict]:
    """把用户编辑写回清单副本。未知字段忽略，类型不合法时拒绝。"""
    if not isinstance(values, dict):
        raise ChecklistError("确认值必须是对象")
    updated = []
    for item in checklist:
        copied = dict(item)
        key = copied.get("field_key")
        if key in values and key in FIELD_BY_KEY:
            copied["user_value"] = normalize_user_value(FIELD_BY_KEY[key], values[key])
        updated.append(copied)
    return updated


def confirm_fields(checklist: list[dict]) -> dict:
    """生成已确认字段快照。异常项必须有用户值，必填项不能为空。"""
    errors = []
    confirmed = {}
    for item in checklist:
        spec = FIELD_BY_KEY.get(item.get("field_key"))
        if spec is None:
            continue
        user_value = item.get("user_value")
        if item.get("status") != "extracted" and user_value is None:
            errors.append(f"{spec.label}需要人工确认")
            continue
        value = item.get("extracted_value") if user_value is None else user_value
        if spec.required and _is_empty(value):
            errors.append(f"{spec.label}为必填")
            continue
        if _is_empty(value):
            confirmed[spec.key] = [] if spec.multiple else ""
        else:
            confirmed[spec.key] = value
    if errors:
        raise ChecklistError("；".join(errors))
    return confirmed


def normalize_user_value(spec: FieldSpec, raw):
    """把 HTTP 提交的确认值规范成字符串或字符串列表。"""
    if raw is None:
        return None
    if spec.multiple:
        items = _string_list(raw if isinstance(raw, list) else str(raw).splitlines())
        if len(items) > USER_LIST_LIMIT:
            raise ChecklistError(f"{spec.label}条目过多")
        if any(len(item) > USER_ITEM_LIMIT for item in items):
            raise ChecklistError(f"{spec.label}单条内容过长")
        return items
    if isinstance(raw, list):
        text = "；".join(_string_list(raw))
    else:
        text = str(raw)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > USER_VALUE_LIMIT:
        raise ChecklistError(f"{spec.label}内容过长")
    return text


def _classify(spec: FieldSpec, value, confidence: float, uncertain: bool) -> tuple[str, str]:
    if spec.required and _is_empty(value):
        return "missing", "必填字段未抽取到内容"
    if not _is_empty(value) and _has_invalid_format(spec.key, value):
        return "invalid_format", _format_note(spec.key)
    if not _is_empty(value) and (uncertain or confidence < CONFIDENCE_UNCERTAIN_BELOW):
        return "uncertain", "置信度较低或文档中的说法不一致"
    return "extracted", ""


def _cross_file_conflicts(files, file_ids: list[str]) -> dict[str, dict]:
    allowed = set(file_ids)
    dut_values: dict[str, str] = {}
    metric_groups: dict[frozenset[str], list[str]] = {}
    if not isinstance(files, list):
        return {}
    for item in files:
        if not isinstance(item, dict) or item.get("file_id") not in allowed:
            continue
        dut = _coerce(item.get("dut"), False)
        if isinstance(dut, str) and dut:
            dut_values.setdefault(_text_key(dut), dut)
        metrics = _coerce(item.get("assessment_metrics"), True)
        if metrics:
            metric_groups.setdefault(frozenset(_metric_key(value) for value in metrics), metrics)
    conflicts = {}
    if len(dut_values) > 1:
        conflicts["dut"] = {"values": list(dut_values.values()), "note": "多份附件对被试品的说法不一致"}
    if len(metric_groups) > 1:
        union: list[str] = []
        seen: set[str] = set()
        for metrics in metric_groups.values():
            for metric in metrics:
                key = _metric_key(metric)
                if key not in seen:
                    seen.add(key)
                    union.append(metric)
        conflicts["assessment_metrics"] = {"values": union, "note": "多份附件对考核指标的说法不一致"}
    return conflicts


def _coerce(raw, multiple: bool):
    if multiple:
        if isinstance(raw, str):
            return _string_list([raw])
        return _string_list(raw)
    if isinstance(raw, list):
        parts = _string_list(raw)
        return "；".join(parts) if parts else None
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", str(raw)).strip()
    return text or None


def _string_list(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    items = []
    for item in raw:
        if item is None:
            continue
        text = re.sub(r"\s+", " ", str(item)).strip()
        if text:
            items.append(text)
    return items


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not _string_list(value)
    return False


def _confidence(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if value < 0 or value > 1:
        return 0.5
    return value


def _excerpt(raw) -> str:
    if not isinstance(raw, str):
        return ""
    return re.sub(r"\s+", " ", raw).strip()[:EXCERPT_LIMIT]


def _source_file_id(raw, file_ids: list[str]) -> str | None:
    if isinstance(raw, str) and raw in file_ids:
        return raw
    if len(file_ids) == 1:
        return file_ids[0]
    return None


def _has_invalid_format(field_key: str, value) -> bool:
    if field_key == "task_code":
        return isinstance(value, str) and not _TASK_CODE.fullmatch(value)
    if field_key == "schedule":
        return isinstance(value, str) and _schedule_has_illegal_date(value)
    if field_key == "assessment_metrics":
        return _metrics_have_illegal_unit(value if isinstance(value, list) else [value])
    return False


def _format_note(field_key: str) -> str:
    if field_key == "task_code":
        return "任务编号包含空格或其它非法字符"
    if field_key == "schedule":
        return "计划节点中的日期不合法"
    return "考核指标包含无法识别的单位"


def _schedule_has_illegal_date(text: str) -> bool:
    for year, month, day in _FULL_DATE.findall(text):
        if not _is_real_date(int(year), int(month), int(day)):
            return True
    for month, day in _MONTH_DAY.findall(text):
        month_number = int(month)
        day_number = int(day)
        if not 1 <= month_number <= 12:
            return True
        max_day = calendar.monthrange(2024, month_number)[1]
        if not 1 <= day_number <= max_day:
            return True
    if any(int(month) > 12 for month in _MONTH.findall(text)):
        return True
    return any(int(day) > 31 for day in _DAY.findall(text))


def _is_real_date(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= 12:
        return False
    return 1 <= day <= calendar.monthrange(year, month)[1]


def _metrics_have_illegal_unit(values) -> bool:
    if not isinstance(values, list):
        return False
    for value in values:
        if not isinstance(value, str):
            continue
        for _number, unit in _METRIC_UNIT.findall(value):
            if not _unit_allowed(unit):
                return True
    return False


def _unit_allowed(unit: str) -> bool:
    normalized = unit.replace("μ", "u").replace("µ", "u").replace("℃", "c").replace("°C", "c")
    normalized = normalized.replace("°", "deg").replace("Ω", "ohm")
    return normalized.lower() in _ALLOWED_UNITS


def _text_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _metric_key(value: str) -> str:
    return _text_key(value)
