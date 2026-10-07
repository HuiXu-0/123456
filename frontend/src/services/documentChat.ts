/**
 * 和文档对话·数据获取层（接口文档 20.8 + 15.4）。
 *
 * 就选中的文档（可多篇）提问 → 后端在文档专属向量集合上检索 → **严格基于文档内容流式作答**、
 * 标出处溯源。复用苏格拉底 SSE 那套（fetch + ReadableStream，EventSource 不支持 POST）：
 *   `data: {"delta":"<片段>"}` 逐条 → `event: done`（携带 sessionId + sources 溯源）；
 *   出错 `event: error` `data: {"code":2001,...}`，前端保留已渲染片段。
 *
 * mock / 无后端 / SSE 失败：回退 documentLearning.mockDocAnswer 本地逐字流式（全链路可跑，
 * 答案严格取自所选文档正文、带 [n] 行内溯源），与「Mock 兜底」纪律一致。
 */
import { ApiError, getToken, USE_REAL_API } from './api'
import { mockDocAnswer, toSourceRefs } from './documentLearning'
import type { SourceRef } from '../components/SourceTrace'

export interface DocChatDone {
  sessionId: string
  sources: SourceRef[]
}

export interface DocChatStreamOptions {
  /** 问答范围：选中的一篇或多篇文档 id。首个为主文档。 */
  documentIds: string[]
  /** 多轮会话上下文，首轮可空（由后端 done 事件带回）。 */
  sessionId?: string
  message: string
  /** 每个增量片段（逐 delta 追加渲染）。 */
  onDelta: (delta: string) => void
}

interface RawSource {
  title: string
  type: string
  confidence: number
}

/** SSE 流式文档问答：正常结束 resolve done（sessionId + sources）；失败 reject。 */
async function realDocChatStream(opts: DocChatStreamOptions, signal?: AbortSignal): Promise<DocChatDone> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const ids = opts.documentIds.filter(Boolean)
  const res = await fetch('/api/v1/document/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      documentId: ids[0],
      documentIds: ids.length > 1 ? ids : undefined,
      sessionId: opts.sessionId,
      message: opts.message,
    }),
    signal,
  })

  // 非流式回包（401 / 1004 / 1001 等信封错误）：解信封抛业务码
  const ctype = res.headers.get('content-type') ?? ''
  if (!ctype.includes('text/event-stream')) {
    let env: { code?: number; message?: string } = {}
    try {
      env = await res.json()
    } catch {
      /* 非 JSON 体走 HTTP 状态兜底 */
    }
    throw new ApiError(env.code ?? res.status, env.message || `请求失败（HTTP ${res.status}）`)
  }
  if (!res.body) throw new ApiError(2001, '当前环境不支持流式读取')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let done: DocChatDone | null = null

  const handleEvent = (block: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const payload = JSON.parse(dataLines.join('\n'))
    if (event === 'done') {
      done = { sessionId: payload.sessionId, sources: toSourceRefs(payload.sources as RawSource[]) }
    } else if (event === 'error') {
      throw new ApiError(payload.code ?? 2001, payload.message || '文档问答生成失败')
    } else if (typeof payload.delta === 'string') {
      opts.onDelta(payload.delta)
    }
  }

  try {
    for (;;) {
      const { value, done: eof } = await reader.read()
      if (eof) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx).replace(/\r/g, '')
        buf = buf.slice(idx + 2)
        if (block.trim()) handleEvent(block)
      }
    }
  } finally {
    reader.cancel().catch(() => {
      /* 流已结束时忽略 */
    })
  }

  if (!done) throw new ApiError(2001, '流式响应未正常结束')
  return done
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/**
 * 和文档对话（逐字流式）。
 * - 联调：真实 SSE，逐 delta 渲染，done 带回 sources；超时/首字节看门狗中止，避免卡死。
 * - mock / 失败兜底：本地逐字吐出严格取自文档的答案（带 [n] 溯源），体感与流式一致。
 * `control.cancelled` 置 true 可随时中止（切换文档 / 发起新问题时）。
 * 返回本轮溯源 sources 与 sessionId（供多轮续话 + SourceTrace 渲染）。
 */
export async function streamDocChat(
  opts: DocChatStreamOptions,
  control: { cancelled: boolean } = { cancelled: false }
): Promise<DocChatDone> {
  const emit = (d: string) => {
    if (!control.cancelled) opts.onDelta(d)
  }

  if (USE_REAL_API) {
    let started = false
    const ac = new AbortController()
    const OVERALL_MS = 30_000
    const IDLE_MS = 12_000
    let idleTimer: ReturnType<typeof setTimeout> | null = null
    const overallTimer = setTimeout(() => ac.abort(), OVERALL_MS)
    const bumpIdle = () => {
      if (idleTimer) clearTimeout(idleTimer)
      idleTimer = setTimeout(() => ac.abort(), IDLE_MS)
    }
    bumpIdle()
    try {
      const done = await realDocChatStream(
        {
          ...opts,
          onDelta: (d) => {
            if (control.cancelled) {
              ac.abort()
              return
            }
            started = true
            bumpIdle()
            emit(d)
          },
        },
        ac.signal
      )
      return done
    } catch (e) {
      console.error('[doc-chat] SSE 失败/超时', e)
      if (control.cancelled) return { sessionId: opts.sessionId ?? '', sources: [] }
      // 已出字：保留片段 + 本轮溯源；未出字：给出友好错误（联调态不回退离线 mock 文案）
      if (started) return { sessionId: opts.sessionId ?? '', sources: mockDocAnswer(opts.documentIds, opts.message).sources }
      emit('抱歉，本次回答生成失败或超时，请稍后重试。')
      return { sessionId: opts.sessionId ?? '', sources: [] }
    } finally {
      clearTimeout(overallTimer)
      if (idleTimer) clearTimeout(idleTimer)
    }
  }

  // 纯离线（VITE_USE_REAL_API=false）：逐字吐出严格取自文档的答案（Mock 兜底全链路可跑）
  const { answer, sources } = mockDocAnswer(opts.documentIds, opts.message)
  for (const ch of answer) {
    if (control.cancelled) return { sessionId: opts.sessionId ?? '', sources }
    emit(ch)
    await sleep(12)
  }
  return { sessionId: opts.sessionId ?? '', sources }
}
