<template>
  <div class="test-task-page">
    <ExtensionDetailLayout
      active-key="checklist"
      :tabs="tabs"
      :loading="loading"
      :ready="Boolean(extraction) || Boolean(loadError)"
      empty-description="未找到试验任务"
    >
      <template #breadcrumb>
        <nav class="extension-detail-breadcrumb" aria-label="试验任务确认清单">
          <button type="button" class="extension-detail-back" @click="backToKnowledge">知识库</button>
          <ChevronRight :size="15" aria-hidden="true" />
          <button type="button" class="extension-detail-back" @click="backToList">
            {{ database.name || kbId }}
          </button>
          <ChevronRight :size="15" aria-hidden="true" />
          <span class="extension-detail-current">{{ extraction?.display_name || '确认清单' }}</span>
        </nav>
      </template>

      <template #panel-checklist>
        <div v-if="extraction" class="checklist">
          <header class="checklist-summary">
            <div>
              <strong>{{ extraction.display_name }}</strong>
              <span class="test-task-status" :data-status="extraction.status">
                {{ extraction.status_label }}
              </span>
              <p>{{ sourceText }}</p>
            </div>
            <div class="checklist-actions">
              <button v-if="canRetry" type="button" :disabled="acting" @click="retry">重新抽取</button>
              <button v-if="canEdit" type="button" :disabled="acting" @click="save">保存修改</button>
              <button v-if="canEdit" type="button" class="primary" :disabled="acting" @click="submit">
                提交确认
              </button>
            </div>
          </header>

          <p v-if="extraction.error_message" class="checklist-alert error">{{ extraction.error_message }}</p>
          <p v-else-if="extraction.task_message && isWorking" class="checklist-alert">
            {{ extraction.task_message }}
          </p>
          <p v-else-if="isWorking" class="checklist-alert">抽取进行中，完成后可以校核字段。</p>
          <p v-else-if="extraction.status === 'confirmed'" class="checklist-alert success">
            已确认。刷新页面后仍会保留这次提交的字段。
          </p>
          <p v-if="formError" class="checklist-alert error">{{ formError }}</p>

          <article v-for="item in extraction.checklist" :key="item.field_key" class="field-card">
            <header>
              <h3>
                {{ item.label }}
                <small v-if="item.required">必填</small>
              </h3>
              <span class="test-task-status" :data-status="item.status">{{ item.status_label }}</span>
            </header>
            <p v-if="item.note" class="field-note">{{ item.note }}</p>
            <label>
              抽取值
              <textarea :value="formatFieldValue(item.extracted_value) || '—'" readonly rows="2" />
            </label>
            <label>
              来源片段
              <textarea :value="item.source_excerpt || '—'" readonly rows="2" />
            </label>
            <p v-if="item.source_file_id" class="field-note">来源文件：{{ fileName(item.source_file_id) }}</p>
            <label>
              确认值
              <textarea
                v-model="drafts[item.field_key]"
                :readonly="!canEdit"
                :placeholder="item.multiple ? '每行一条' : '填写确认后的内容'"
                rows="3"
              />
            </label>
          </article>
        </div>
        <p v-else-if="loadError" class="checklist-alert error">{{ loadError }}</p>
      </template>
    </ExtensionDetailLayout>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronRight, ListChecks } from '@lucide/vue'
import { message } from 'ant-design-vue'
import ExtensionDetailLayout from '@/components/shared/ExtensionDetailLayout.vue'
import { testTaskApi } from '@/apis/test_task_api'
import { useDatabaseStore } from '@/stores/database'
import { formatFieldValue, initialDraft, validateDrafts, valuesFromDrafts } from '@/utils/testTaskChecklist'

const route = useRoute()
const router = useRouter()
const store = useDatabaseStore()
const tabs = [{ key: 'checklist', label: '确认清单', icon: ListChecks }]

const kbId = computed(() => String(route.params.kbId || ''))
const extractionId = computed(() => String(route.params.extractionId || ''))
const database = computed(() => store.database || {})
const canManage = computed(() => database.value?.can_manage === true)

const extraction = ref(null)
const drafts = ref({})
const loading = ref(false)
const acting = ref(false)
const loadError = ref('')
const formError = ref('')
let pollTimer = null
let disposed = false

const isWorking = computed(() => ['queued', 'running'].includes(extraction.value?.status))
const canEdit = computed(() => canManage.value && extraction.value?.status === 'ready')
const canRetry = computed(
  () => canManage.value && ['failed', 'queued'].includes(extraction.value?.status)
)
const sourceText = computed(() => {
  const names = (extraction.value?.source_files || []).map((file) => file.filename).filter(Boolean)
  return names.length ? `来源：${names.join('、')}` : '未关联附件'
})

const fileName = (fileId) =>
  extraction.value?.source_files?.find((file) => file.file_id === fileId)?.filename || fileId

const resetDrafts = (record) => {
  drafts.value = Object.fromEntries((record.checklist || []).map((item) => [item.field_key, initialDraft(item)]))
}

const stopPolling = () => {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

const schedulePolling = () => {
  stopPolling()
  if (!isWorking.value || disposed) return
  pollTimer = setTimeout(async () => {
    await load(true)
    schedulePolling()
  }, 2000)
}

const load = async (silent = false) => {
  if (!silent) loading.value = true
  loadError.value = ''
  try {
    const record = await testTaskApi.get(kbId.value, extractionId.value)
    if (disposed) return
    const statusChanged = extraction.value?.status !== record.status
    extraction.value = record
    if (!silent || statusChanged) resetDrafts(record)
  } catch (error) {
    if (!silent) {
      extraction.value = null
      loadError.value = error.message || '加载确认清单失败'
    }
  } finally {
    if (!silent) loading.value = false
  }
}

const save = async () => {
  formError.value = ''
  acting.value = true
  try {
    const record = await testTaskApi.saveFields(
      kbId.value,
      extractionId.value,
      valuesFromDrafts(extraction.value.checklist, drafts.value)
    )
    extraction.value = record
    resetDrafts(record)
    message.success('已保存修改')
  } catch (error) {
    message.error(error.message || '保存失败')
  } finally {
    acting.value = false
  }
}

const submit = async () => {
  const errors = validateDrafts(extraction.value.checklist, drafts.value)
  if (errors.length) {
    formError.value = errors.join('；')
    return
  }
  formError.value = ''
  acting.value = true
  try {
    const record = await testTaskApi.confirm(
      kbId.value,
      extractionId.value,
      valuesFromDrafts(extraction.value.checklist, drafts.value)
    )
    extraction.value = record
    resetDrafts(record)
    message.success('已提交确认')
  } catch (error) {
    message.error(error.message || '提交确认失败，请检查清单后重试')
  } finally {
    acting.value = false
  }
}

const retry = async () => {
  acting.value = true
  formError.value = ''
  try {
    extraction.value = await testTaskApi.retry(kbId.value, extractionId.value)
    resetDrafts(extraction.value)
    if (extraction.value.status === 'failed') {
      message.error(extraction.value.error_message || '重新抽取失败')
    } else if (!extraction.value.durable_task_id) {
      message.error('抽取任务没有投递成功，请再试一次')
    } else if (extraction.value.status === 'queued') {
      message.info(extraction.value.task_message || '抽取仍在排队')
      schedulePolling()
    } else {
      message.success('已重新发起抽取')
      schedulePolling()
    }
  } catch (error) {
    message.error(error.message || '重新抽取失败')
  } finally {
    acting.value = false
  }
}

const backToKnowledge = () => {
  router.push({ path: '/extensions', query: { tab: 'knowledge' } })
}

const backToList = () => {
  router.push({
    name: 'ExtensionKnowledgeBaseDetail',
    params: { kbId: kbId.value },
    query: { section: 'test-tasks' }
  })
}

onMounted(async () => {
  store.kbId = kbId.value
  await Promise.all([store.getDatabaseInfo(kbId.value, true), load()])
  schedulePolling()
})

onUnmounted(() => {
  disposed = true
  stopPolling()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.test-task-page {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.checklist {
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: var(--color-text);
}

.checklist-summary,
.field-card,
.checklist-actions button {
  border: 1px solid var(--gray-200);
  background: var(--color-bg-container);
}

.checklist-summary {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-radius: 12px;
  padding: 16px;
}

.checklist-summary p,
.field-note {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
}

.checklist-actions {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.checklist-actions button,
.field-card textarea {
  border-radius: 8px;
}

.checklist-actions button {
  padding: 6px 12px;
  cursor: pointer;
  color: inherit;
}

.checklist-actions button.primary {
  background: var(--main-700);
  color: var(--main-0);
  border-color: var(--main-700);
}

.checklist-alert {
  margin: 0;
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--main-30);
}

.checklist-alert.error {
  color: var(--color-error-700);
  background: var(--color-error-50);
}

.checklist-alert.success {
  color: var(--color-success-700);
  background: var(--color-success-50);
}

.field-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-radius: 12px;
  padding: 14px 16px;
}

.field-card header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.field-card h3 {
  margin: 0;
  font-size: 15px;
}

.field-card small {
  margin-left: 6px;
  color: var(--color-error-700);
  font-weight: 500;
}

.field-card label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.field-card textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid var(--gray-200);
  background: var(--color-bg-elevated);
  color: var(--color-text);
  padding: 8px 10px;
}

.test-task-status[data-status='extracted'],
.test-task-status[data-status='confirmed'] {
  color: var(--color-success-700);
}

.test-task-status[data-status='uncertain'],
.test-task-status[data-status='invalid_format'],
.test-task-status[data-status='ready'] {
  color: var(--color-warning-700, #d48806);
}

.test-task-status[data-status='missing'],
.test-task-status[data-status='conflict'],
.test-task-status[data-status='failed'] {
  color: var(--color-error-700);
}

@media (max-width: 800px) {
  .checklist-summary {
    flex-direction: column;
  }
}
</style>
