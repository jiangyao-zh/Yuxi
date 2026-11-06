# 试验任务结构化抽取与确认清单

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/test_task_service.py

## 问题

试验任务书、年度计划和产品规格书在知识库中只能得到 Markdown。后续业务需要稳定的试验任务字段，同时必须让人处理缺失、低置信、格式异常和多附件冲突。这些文档只是知识库文件中的一部分，不能把每次解析都变成试验任务抽取。

## 决策

试验任务是知识库上的独立记录，引用已有 `kb_id` 与已解析文件，不新增上传入口。用户从文件管理对状态已解析且存在 Markdown 的文件手动发起抽取。知识库详情提供试验任务列表，确认清单页展示字段、抽取值、来源片段、状态和可编辑确认值。

字段规则由 `yuxi.knowledge.test_tasks.fields` 固定：必填为试验任务名称、试验目的、被试品和考核指标；考核指标、试验项目、试验条件、依据标准和关联附件类型可多条。必填为空记为缺失；置信度低于 0.6 或模型标记不确定记为存疑；任务编号、计划和指标单位不符合明确格式规则记为格式异常；多份附件对被试品或考核指标的非空说法不一致记为冲突。异常项必须带上用户确认值后才能提交，必填确认值不能为空。用户修正后的值不再被格式规则拒绝。

抽取使用该知识库已配置的 `llm_model_spec`，经现有 `select_model` 调用聊天模型。模型输出必须是约定 JSON；解析失败使 Durable Task 失败，错误写在试验任务记录上，用户可以重新抽取。成功和失败都在 Task 终态事务中更新同一条抽取记录。已确认记录不会被迟到失败覆盖。图谱抽取器注册表不接收试验任务类型，`knowledge_graph_index` 仍指向原图谱 Handler。

knowledge schema 为 v3。v2 只创建 `test_task_extractions`；未版本化 baseline 由当前建表和 `ensure_knowledge_schema` 带上同一张表。读取沿用知识库读权限，发起、编辑、确认和重试沿用管理权限。

## 替代方案

- 解析成功后自动抽取：会把无关文档送进模型，并改变现有解析时延和失败面。
- 把抽取做成对话 Skill：不能形成可刷新的确认记录，也无法复用知识库权限和 Durable Task。
- 把试验任务字段写入知识文件 `processing_params`：文件状态和试验任务确认状态会被绑成同一个生命周期。
- 在图谱抽取器工厂注册新类型：图谱构建会把试验任务 schema 当成实体关系抽取。

## 后果

- 已确认字段保存在 `confirmed_fields`，清单上的抽取状态和来源片段继续保留。
- 单份 Markdown 进入模型前截断到 8000 字，更长正文不会整篇抽取。
- 单次最多 8 个已解析文件。知识库没有配置 LLM，或文件还没有 Markdown 时，请求直接失败。
- 已确认试验任务目前只供读取和后续业务使用，没有接入 Agent 工具或自动派发。
- 运行中的环境要把 knowledge schema 从 v2 升到 v3 后，API 与 worker 才能通过当前版本校验。

## 验证

- `docker compose exec api uv run --group test pytest test/unit/knowledge/test_test_task_fields.py test/unit/services/test_storage_migration.py test/unit/services/test_task_registry.py -m "not slow"`
- `docker compose exec api uv run --group test pytest test/integration/services/test_schema_migration_version.py -m integration`
- `docker compose exec web pnpm run test:unit -- test/unit/test_task_checklist.test.js`
- 负向案例：缺必填不能确认；未解析文件和未配置 LLM 不投递任务；模型 JSON 失败向上抛出；已确认记录不被失败 hook 改回失败；未知抽取状态不能写入 PostgreSQL；图谱工厂拒绝 `test_task` 类型。
