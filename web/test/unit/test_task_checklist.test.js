import assert from 'node:assert/strict'
import test from 'node:test'

import { canExtractTestTask } from '../../src/utils/knowledge_file_policy.js'
import { initialDraft, validateDrafts, valuesFromDrafts } from '../../src/utils/testTaskChecklist.js'

test('只有已解析文档可以发起试验任务抽取', () => {
  assert.equal(canExtractTestTask({ file_id: 'f1', status: 'parsed', is_folder: false }), true)
  assert.equal(canExtractTestTask({ file_id: 'f1', status: 'indexed', is_folder: false }), true)
  assert.equal(canExtractTestTask({ file_id: 'f1', status: 'uploaded', is_folder: false }), false)
  assert.equal(canExtractTestTask({ file_id: 'f1', status: 'parsed', is_folder: true }), false)
  assert.equal(
    canExtractTestTask({ file_id: 'f1', status: 'parsed', is_folder: false, has_parsed_markdown: false }),
    false
  )
})

test('异常必填项为空时不能提交，补全后可以生成确认值', () => {
  const items = [
    { field_key: 'task_name', label: '试验任务名称', required: true, multiple: false, status: 'missing' },
    {
      field_key: 'assessment_metrics',
      label: '考核指标',
      required: true,
      multiple: true,
      status: 'invalid_format',
      extracted_value: ['100 xyz']
    }
  ]
  const emptyDrafts = { task_name: '', assessment_metrics: '' }
  assert.deepEqual(validateDrafts(items, emptyDrafts), ['试验任务名称为必填', '考核指标为必填'])

  const drafts = { task_name: '  高温试验  ', assessment_metrics: '温度不超过 85℃\n\n湿度 90%' }
  assert.deepEqual(validateDrafts(items, drafts), [])
  assert.deepEqual(valuesFromDrafts(items, drafts), {
    task_name: '高温试验',
    assessment_metrics: ['温度不超过 85℃', '湿度 90%']
  })
})

test('冲突字段不预填抽取值，已抽取字段保留原文', () => {
  assert.equal(
    initialDraft({
      field_key: 'dut',
      multiple: false,
      status: 'conflict',
      extracted_value: ['控制器甲', '控制器乙'],
      user_value: null
    }),
    ''
  )
  assert.equal(
    initialDraft({
      field_key: 'assessment_metrics',
      multiple: true,
      status: 'conflict',
      extracted_value: ['加速度 5g', '加速度 12g'],
      user_value: null
    }),
    ''
  )
  assert.deepEqual(
    validateDrafts(
      [
        {
          field_key: 'assessment_metrics',
          label: '考核指标',
          required: true,
          multiple: true,
          status: 'conflict'
        }
      ],
      { assessment_metrics: '' }
    ),
    ['考核指标为必填']
  )
  assert.equal(
    initialDraft({
      field_key: 'task_name',
      multiple: false,
      status: 'extracted',
      extracted_value: '淋雨试验',
      user_value: null
    }),
    '淋雨试验'
  )
  assert.equal(
    initialDraft({
      field_key: 'task_name',
      multiple: false,
      status: 'extracted',
      extracted_value: '淋雨试验',
      user_value: '人工名称'
    }),
    '人工名称'
  )
})
