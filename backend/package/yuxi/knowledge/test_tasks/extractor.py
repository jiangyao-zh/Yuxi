"""用知识库已配置的聊天模型抽取试验任务字段。"""

from __future__ import annotations

import json
import re

from yuxi.knowledge.test_tasks.fields import FIELD_SPECS
from yuxi.models.chat import select_model

MARKDOWN_LIMIT = 8000

_PROMPT = """你是试验任务文档的结构化抽取器。只根据给定 Markdown 抽取字段，文档没有的内容填 null，不要编造。
无法确定、同一文档前后不一致或把握不足时，把 uncertain 设为 true，并把 confidence 设为 0 到 1 的小数。
多条字段使用字符串数组。excerpt 必须是对应文档中的连续原文，不超过 200 字。file_id 必须使用文档标题里的 file_id。
files 按每份文档分别给出被试品和考核指标，便于发现附件冲突。只返回一个 JSON 对象，不要 Markdown。

JSON 结构：
{{
  "fields": {{
    {field_lines}
  }},
  "files": [
    {{"file_id": "", "dut": "", "assessment_metrics": []}}
  ]
}}
"""


def build_extraction_prompt(documents: list[dict]) -> str:
    """组装带字段约束和原文的抽取提示。"""
    field_lines = ",\n    ".join(
        f'"{spec.key}": {{"value": null, "confidence": 0, "excerpt": "", "file_id": "", "uncertain": false}}'
        for spec in FIELD_SPECS
    )
    sections = [_PROMPT.format(field_lines=field_lines), "字段："]
    sections.extend(f"- {spec.key} {spec.label}{'，必填' if spec.required else '，选填'}" for spec in FIELD_SPECS)
    sections.append("文档：")
    for document in documents:
        markdown = document["markdown"]
        truncated = len(markdown) >= MARKDOWN_LIMIT
        text = markdown[:MARKDOWN_LIMIT]
        note = "（原文已截断）" if truncated else ""
        sections.append(f"\n## file_id={document['file_id']} filename={document['filename']}{note}\n{text}")
    return "\n".join(sections)


def parse_model_json(text: str) -> dict:
    """解析模型返回的 JSON 对象。无法解析时抛出 ValueError。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("模型没有返回抽取结果")
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型返回的不是 JSON 对象")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("模型返回的 JSON 无法解析") from exc
    if not isinstance(payload, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return payload


async def extract_test_task_fields(model_spec: str, documents: list[dict]) -> dict:
    """调用知识库聊天模型并返回结构化抽取 JSON。"""
    model = select_model(model_spec=model_spec)
    response = await model.call(build_extraction_prompt(documents), stream=False)
    return parse_model_json(getattr(response, "content", ""))
