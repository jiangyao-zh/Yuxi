import { apiAdminGet, apiAdminPost, apiAdminPut } from './base'

export const testTaskApi = {
  list: (kbId) => apiAdminGet(`/api/knowledge/databases/${kbId}/test-tasks`),

  create: (kbId, fileIds) =>
    apiAdminPost(`/api/knowledge/databases/${kbId}/test-tasks`, { file_ids: fileIds }),

  get: (kbId, extractionId) =>
    apiAdminGet(`/api/knowledge/databases/${kbId}/test-tasks/${extractionId}`),

  saveFields: (kbId, extractionId, values) =>
    apiAdminPut(`/api/knowledge/databases/${kbId}/test-tasks/${extractionId}/fields`, { values }),

  confirm: (kbId, extractionId, values) =>
    apiAdminPost(`/api/knowledge/databases/${kbId}/test-tasks/${extractionId}/confirm`, { values }),

  retry: (kbId, extractionId) =>
    apiAdminPost(`/api/knowledge/databases/${kbId}/test-tasks/${extractionId}/retry`, {})
}
