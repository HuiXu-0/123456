/**
 * 生成模型管理（接口文档 21，additive 新增）
 *
 * - GET    /models                 可选模型注册表 + 当前模型（21.1，含用户自建配置）
 * - PUT    /models/current         切换当前生成模型（21.2；自建配置仅本人生效）
 * - POST   /models/configs         新增自建模型配置（21.3，key 后端加密落库）
 * - PUT    /models/configs/{id}    编辑自建配置（21.3；apiKey 缺省=保留原 key）
 * - DELETE /models/configs/{id}    删除自建配置（21.3）
 * - POST   /models/configs/test    测试连通性（21.4）
 *
 * key 安全（CC-model-management-page.md §3）：apiKey 仅在提交/测试的请求体中出现一次，
 * **不进 localStorage、不进任何持久化 store**；后端一律返回脱敏形（****后四位）。
 * 未配 Key 的模型仍可选：后端调用时自动回落默认模型（优雅降级，绝不崩）。
 * USE_REAL_API=false 时返回本地演示数据（与后端注册表同构，key 同样只存脱敏形）。
 */
import { apiGet, apiPost, apiPut, apiRequest, USE_REAL_API } from './api'

export interface ModelInfo {
  id: string
  label: string
  provider: 'mock' | 'deepseek' | 'modelscope' | 'openai'
  /** 是否已配置该 provider 的 Key（未配也可选，生成时自动回落默认模型） */
  available: boolean
  isCurrent: boolean
  /** —— 以下为 21.1 additive 扩展字段（旧后端不返回时留空即可） —— */
  /** 上游 API 的 model 参数 */
  modelId?: string
  baseUrl?: string
  /** builtin=内置（.env 驱动）/ custom=用户界面自建（可编辑/删除） */
  source?: 'builtin' | 'custom'
  /** 自建配置的脱敏 key（****后四位），内置条目无此字段 */
  apiKeyMasked?: string
}

export interface ModelRegistry {
  models: ModelInfo[]
  current: string
}

/** 自建模型配置提交体（21.3）。apiKey 提交后不在前端任何地方留存。 */
export interface ModelConfigInput {
  label: string
  provider: 'openai' | 'modelscope' | 'deepseek'
  baseUrl: string
  modelId: string
  /** 新增必填；编辑时留空 = 保留原 key */
  apiKey?: string
}

/** 自建配置对外形态（21.3 响应，key 已脱敏） */
export interface ModelConfigInfo {
  id: string
  label: string
  provider: string
  baseUrl: string
  modelId: string
  apiKeyMasked: string
  createdAt?: string | null
  updatedAt?: string | null
}

/** 连通性测试结果（21.4） */
export interface ModelTestResult {
  ok: boolean
  latencyMs: number
  message: string
}

/* ------------------------------------------------------------------ */
/* 纯前端 mock 演示数据（VITE_USE_REAL_API=false 时使用，可本地增删切换） */
/* ------------------------------------------------------------------ */
let mockCurrent = 'deepseek-chat'
const MOCK_MODELS: Omit<ModelInfo, 'isCurrent'>[] = [
  { id: 'deepseek-chat', label: 'DeepSeek（官方 · 默认）', provider: 'deepseek', available: true, modelId: 'deepseek-chat', baseUrl: 'https://api.deepseek.com', source: 'builtin' },
  { id: 'ZhipuAI/GLM-4.6', label: 'GLM-4.6（魔搭）', provider: 'modelscope', available: false, modelId: 'ZhipuAI/GLM-4.6', baseUrl: 'https://api-inference.modelscope.cn/v1', source: 'builtin' },
  { id: 'Qwen/Qwen3-32B', label: 'Qwen3-32B（魔搭）', provider: 'modelscope', available: false, modelId: 'Qwen/Qwen3-32B', baseUrl: 'https://api-inference.modelscope.cn/v1', source: 'builtin' },
  { id: 'deepseek-ai/DeepSeek-V3.1', label: 'DeepSeek-V3.1（魔搭）', provider: 'modelscope', available: false, modelId: 'deepseek-ai/DeepSeek-V3.1', baseUrl: 'https://api-inference.modelscope.cn/v1', source: 'builtin' },
]
/** mock 自建配置（内存态，仅存脱敏 key —— 与真实链路同口径，不留明文） */
let mockCustoms: ModelConfigInfo[] = []
let mockSeq = 0

const mockRegistry = (): ModelRegistry => ({
  models: [
    ...MOCK_MODELS.map((m) => ({ ...m, isCurrent: m.id === mockCurrent })),
    ...mockCustoms.map((c) => ({
      id: c.id,
      label: c.label,
      provider: c.provider as ModelInfo['provider'],
      available: true,
      isCurrent: c.id === mockCurrent,
      modelId: c.modelId,
      baseUrl: c.baseUrl,
      source: 'custom' as const,
      apiKeyMasked: c.apiKeyMasked,
    })),
  ],
  current: mockCurrent,
})

const maskKey = (key: string) => `****${key.slice(-4)}`

/* ------------------------------------------------------------------ */
/* 对外 API                                                            */
/* ------------------------------------------------------------------ */

/** 拉取可选模型列表 + 当前模型 */
export async function fetchModelRegistry(): Promise<ModelRegistry> {
  if (!USE_REAL_API) return Promise.resolve(mockRegistry())
  return apiGet<ModelRegistry>('/models')
}

/** 切换当前生成模型，返回切换后的注册表快照 */
export async function switchModel(modelId: string): Promise<ModelRegistry> {
  if (!USE_REAL_API) {
    mockCurrent = modelId
    return Promise.resolve(mockRegistry())
  }
  return apiPut<ModelRegistry>('/models/current', { modelId })
}

/** 新增自建模型配置（21.3）。apiKey 只随本次请求提交，前端不留存。 */
export async function addModelConfig(input: Required<ModelConfigInput>): Promise<ModelConfigInfo> {
  if (!USE_REAL_API) {
    const cfg: ModelConfigInfo = {
      id: `umc_mock_${++mockSeq}`,
      label: input.label,
      provider: input.provider,
      baseUrl: input.baseUrl,
      modelId: input.modelId,
      apiKeyMasked: maskKey(input.apiKey),
    }
    mockCustoms = [...mockCustoms, cfg]
    return Promise.resolve(cfg)
  }
  return apiPost<ModelConfigInfo>('/models/configs', input)
}

/** 编辑自建配置（21.3）。apiKey 留空 = 保留原 key。 */
export async function updateModelConfig(id: string, input: ModelConfigInput): Promise<ModelConfigInfo> {
  if (!USE_REAL_API) {
    mockCustoms = mockCustoms.map((c) =>
      c.id === id
        ? {
            ...c,
            label: input.label,
            provider: input.provider,
            baseUrl: input.baseUrl,
            modelId: input.modelId,
            apiKeyMasked: input.apiKey ? maskKey(input.apiKey) : c.apiKeyMasked,
          }
        : c
    )
    const hit = mockCustoms.find((c) => c.id === id)
    if (!hit) throw new Error('模型配置不存在')
    return Promise.resolve(hit)
  }
  return apiPut<ModelConfigInfo>(`/models/configs/${encodeURIComponent(id)}`, input)
}

/** 删除自建配置（21.3）。删除当前使用的配置后端会自动回落默认模型。 */
export async function deleteModelConfig(id: string): Promise<void> {
  if (!USE_REAL_API) {
    mockCustoms = mockCustoms.filter((c) => c.id !== id)
    if (mockCurrent === id) mockCurrent = 'deepseek-chat'
    return Promise.resolve()
  }
  await apiRequest(`/models/configs/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** 测试连通性（21.4）：configId 测已保存配置（用后端解密 key，可带 baseUrl/modelId 覆盖），或直接传表单值。 */
export async function testModelConfig(
  target:
    | { configId: string; baseUrl?: string; modelId?: string }
    | { baseUrl: string; modelId: string; apiKey: string }
): Promise<ModelTestResult> {
  if (!USE_REAL_API) {
    // mock 演示：本地确定性返回成功（无网络）
    return Promise.resolve({ ok: true, latencyMs: 128, message: '连接成功（128ms，离线演示）' })
  }
  return apiPost<ModelTestResult>('/models/configs/test', target)
}
