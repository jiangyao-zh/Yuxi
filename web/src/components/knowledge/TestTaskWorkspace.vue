<template>
  <section class="test-task-workspace">
    <header class="test-task-header">
      <div>
        <h3>试验任务</h3>
        <p>
          选择已解析的试验任务书、年度计划或产品规格书，抽取字段后在确认清单中校核。
          <template v-if="!canManage">当前权限可以查看清单。</template>
        </p>
      </div>
      <button type="button" class="test-task-refresh" @click="load">刷新</button>
    </header>

    <p v-if="loadError" class="test-task-error">{{ loadError }}</p>
    <p v-else-if="loading" class="test-task-muted">正在加载试验任务…</p>
    <p v-else-if="items.length === 0" class="test-task-muted">
      还没有试验任务。在文件管理中选择已解析文档，使用「试验任务解析」。
    </p>

    <ul v-else class="test-task-list">
      <li v-for="item in items" :key="item.extraction_id">
        <button type="button" class="test-task-row" @click="openChecklist(item)">
          <span class="test-task-name">{{ item.display_name }}</span>
          <span class="test-task-status" :data-status="item.status">{{ item.status_label }}</span>
          <span class="test-task-meta">
            {{ sourceLabel(item) }}
            <template v-if="item.status === 'ready' || item.status === 'confirmed'">
              · {{ item.issue_count }} 项待关注
            </template>
          </span>
          <span class="test-task-time">{{ formatTime(item.updated_at || item.created_at) }}</span>
        </button>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { testTaskApi } from '@/apis/test_task_api'

const props = defineProps({
  kbId: { type: String, required: true },
  canManage: { type: Boolean, default: false }
})

const router = useRouter()
const items = ref([])
const loading = ref(false)
const loadError = ref('')

const formatTime = (value) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const sourceLabel = (item) => {
  const names = (item.source_files || []).map((file) => file.filename).filter(Boolean)
  if (names.length === 0) return '未关联附件'
  if (names.length === 1) return names[0]
  return `${names[0]} 等 ${names.length} 个文件`
}

const load = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const response = await testTaskApi.list(props.kbId)
    items.value = response.items || []
  } catch (error) {
    loadError.value = error.message || '加载试验任务失败'
  } finally {
    loading.value = false
  }
}

const openChecklist = (item) => {
  router.push({
    name: 'ExtensionTestTaskChecklist',
    params: { kbId: props.kbId, extractionId: item.extraction_id }
  })
}

watch(
  () => props.kbId,
  () => {
    load()
  }
)

onMounted(load)
</script>

<style scoped>
.test-task-workspace {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 240px;
  color: var(--color-text);
}

.test-task-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.test-task-header h3 {
  margin: 0 0 6px;
  font-size: 16px;
}

.test-task-header p,
.test-task-muted {
  margin: 0;
  color: var(--color-text-secondary);
}

.test-task-refresh,
.test-task-row {
  border: 1px solid var(--gray-200);
  background: var(--color-bg-container);
  color: inherit;
}

.test-task-refresh {
  border-radius: 8px;
  padding: 6px 12px;
  cursor: pointer;
}

.test-task-error {
  margin: 0;
  color: var(--color-error-700);
}

.test-task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.test-task-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(160px, 1.4fr) 88px minmax(160px, 1fr) 180px;
  gap: 12px;
  align-items: center;
  text-align: left;
  border-radius: 10px;
  padding: 12px 14px;
  cursor: pointer;
}

.test-task-name {
  font-weight: 600;
}

.test-task-meta,
.test-task-time {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.test-task-status[data-status='ready'] {
  color: var(--color-warning-700, #d48806);
}

.test-task-status[data-status='confirmed'] {
  color: var(--color-success-700);
}

.test-task-status[data-status='failed'],
.test-task-status[data-status='missing'],
.test-task-status[data-status='conflict'] {
  color: var(--color-error-700);
}

@media (max-width: 800px) {
  .test-task-row {
    grid-template-columns: 1fr;
  }
}
</style>
