/**
 * 统一 API 封装（P0 联调数据获取层）。
 *
 * 职责：
 * - 解构后端统一响应信封 `{code, message, data, traceId}`，对上层只暴露 `data`；
 * - 自动附带 `Authorization: Bearer <token>`；
 * - 业务码非 0 统一抛 {@link ApiError}；401 / code 1002 时清票并跳回登录入口。
 *
 * 总开关 {@link USE_REAL_API} 由 `VITE_USE_REAL_API` 环境变量控制：
 * 为 'true' 时各页面 / store 走本封装请求真实后端，否则保持现有 mock。
 */

/** 联调总开关：true=走真实后端；false 或未设=回退现有 mock */
export const USE_REAL_API = import.meta.env.VITE_USE_REAL_API === 'true'

const BASE = '/api/v1'
const TOKEN_KEY = 'zx-token'
const USER_KEY = 'zx-user'

/** 登录用户（接口文档 3.1 + 15.1） */
export interface AuthUser {
  userId: string
  username: string
  displayName: string
  role: 'learner' | 'admin'
  hasDiagnosed: boolean
  hasGeneratedPath: boolean
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* 忽略隐私模式写入失败 */
  }
}

export function setUser(user: AuthUser): void {
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    /* 忽略 */
  }
}

export function getUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    /* 忽略 */
  }
}

/**
 * 判断 JWT 是否已过期（解析 payload.exp，单位秒）。用于刷新后恢复登录态：
 * token 存在且未过期才恢复，过期则照常跳登录。无法解析 / 无 exp 时**不主动判过期**
 * （返回 false），把最终裁决交给后端 401/1002 兜底，避免误登出。无 token → 视为过期。
 */
export function isTokenExpired(token: string | null = getToken()): boolean {
  if (!token) return true
  try {
    const part = token.split('.')[1] ?? ''
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const payload = JSON.parse(atob(padded)) as { exp?: number }
    if (typeof payload.exp !== 'number') return false
    return payload.exp * 1000 <= Date.now()
  } catch {
    return false
  }
}

/** 业务错误（信封 code 非 0）。携带 code 便于上层分支处理。 */
export class ApiError extends Error {
  code: number
  constructor(code: number, message: string) {
    super(message)
    this.code = code
    this.name = 'ApiError'
  }
}

interface Envelope<T> {
  code: number
  message: string
  data: T
  traceId?: string
}

interface RequestOptions {
  method?: string
  /** JSON 请求体（与 form 互斥） */
  body?: unknown
  /** multipart 表单体（浏览器自动设置 boundary） */
  form?: FormData
  /**
   * 额外视为"成功"的业务码白名单：命中时不抛 ApiError，直接返回 data。
   * 用于"降级但携带可用数据"的契约（如 5.2 岗位快照 code 2002 + data.offline）。
   */
  okCodes?: number[]
}

/**
 * 401 / 1002 处理：清除登录态并回到应用入口重新登录。
 * 当前前端无独立登录路由（App 以 stage 状态机切换），故清票后整页刷新回入口；
 * P0 happy path 全程持有有效 token，不会触发。
 */
function onUnauthorized(): void {
  clearAuth()
  try {
    window.dispatchEvent(new Event('zx:unauthorized'))
    window.location.reload()
  } catch {
    /* 非浏览器环境忽略 */
  }
}

/** 发起请求并解信封；成功返回 data，失败抛 ApiError。 */
export async function apiRequest<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  return (await apiRequestWithCode<T>(path, opts)).data
}

/**
 * 同 {@link apiRequest}，但连业务码一起返回：供需要感知降级码的调用方使用
 * （如 5.1/5.2 经 okCodes 放行 2002 后，据 code 区分「正常 / 离线降级」）。
 */
export async function apiRequestWithCode<T = unknown>(
  path: string,
  opts: RequestOptions = {}
): Promise<{ code: number; data: T }> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  let body: BodyInit | undefined
  if (opts.form) {
    body = opts.form
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }

  const method = opts.method ?? (body ? 'POST' : 'GET')
  const res = await fetch(BASE + path, { method, headers, body })

  let env: Envelope<T>
  try {
    env = (await res.json()) as Envelope<T>
  } catch {
    if (res.status === 401) onUnauthorized()
    throw new ApiError(res.status, `请求失败（HTTP ${res.status}）`)
  }

  if (env.code === 1002 || res.status === 401) {
    onUnauthorized()
    throw new ApiError(1002, env.message || '未认证或登录已失效')
  }
  if (env.code !== 0 && !opts.okCodes?.includes(env.code)) {
    throw new ApiError(env.code, env.message || '请求失败')
  }
  return { code: env.code, data: env.data }
}

export const apiGet = <T>(path: string): Promise<T> => apiRequest<T>(path, { method: 'GET' })
export const apiPost = <T>(path: string, body?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: 'POST', body })
export const apiPut = <T>(path: string, body?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: 'PUT', body })
export const apiPostForm = <T>(path: string, form: FormData): Promise<T> =>
  apiRequest<T>(path, { method: 'POST', form })

/* ==================== 异步任务轮询 ==================== */

export interface TaskStatus {
  taskId: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  progress?: number
  result?: unknown
  error?: { code: number; message: string }
}

/**
 * 轮询任务状态直到终态（succeeded / failed），返回最终 TaskStatus。
 * @param taskId 任务 ID
 * @param intervalMs 轮询间隔（默认 2 秒）
 * @param timeoutMs 超时（默认 5 分钟）
 * @param onProgress 进度回调（每轮轮询时调用）
 */
export async function pollTask<T = unknown>(
  taskId: string,
  opts: {
    intervalMs?: number
    timeoutMs?: number
    onProgress?: (progress: number | null, status: string) => void
    signal?: AbortSignal
  } = {}
): Promise<{ status: string; result?: T; error?: { code: number; message: string } }> {
  const { intervalMs = 2000, timeoutMs = 300_000, onProgress, signal } = opts
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    if (signal?.aborted) throw new ApiError(2001, '任务轮询已取消')

    const task = await apiRequest<TaskStatus>(`/tasks/${taskId}`)
    onProgress?.(task.progress ?? null, task.status)

    if (task.status === 'succeeded') {
      return { status: 'succeeded', result: task.result as T }
    }
    if (task.status === 'failed') {
      return { status: 'failed', error: task.error }
    }

    await new Promise((r) => setTimeout(r, intervalMs))
  }

  throw new ApiError(2001, '任务轮询超时')
}

/** 提交异步生成任务，返回 taskId。 */
export async function submitAsyncTask(path: string, body?: unknown): Promise<string> {
  const data = await apiRequest<{ taskId: string; status: string }>(path, {
    method: 'POST',
    body,
  })
  return data.taskId
}
