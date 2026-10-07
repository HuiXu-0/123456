/**
 * 苏格拉底辅导数据获取层（接口文档 8.7 + 15.4，接口 22）。
 *
 * 请求头带 `Accept: text/event-stream` 时后端切 SSE 流式：
 *   `data: {"delta":"<片段>"}` 逐条（空行分隔）→ `event: done`（携带 sessionId +
 *   suggestions）；出错时 `event: error` `data: {"code":2001,...}`，前端保留已渲染
 *   片段并回退本地引导链。EventSource 不支持 POST，故用 fetch + ReadableStream 手工解析。
 */
import { ApiError, getToken, USE_REAL_API } from './api'

export interface TutorDone {
  sessionId: string
  suggestions: string[]
}

export interface TutorStreamOptions {
  kpId: string
  /** 多轮会话上下文，首轮可空（由后端生成并经 done 事件带回）。 */
  sessionId?: string
  message: string
  /** 每个增量片段（逐 delta 追加渲染）。 */
  onDelta: (delta: string) => void
}

/** SSE 流式对话：正常结束 resolve done 负载；连接失败 / event:error 时 reject。
 *  可选 `signal`：用于超时/取消中止底层 fetch（避免后端挂起时 reader.read() 永久阻塞）。 */
export async function tutorChatStream(opts: TutorStreamOptions, signal?: AbortSignal): Promise<TutorDone> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch('/api/v1/resource/tutor/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({ kpId: opts.kpId, sessionId: opts.sessionId, message: opts.message }),
    signal,
  })

  // 非流式回包（401 / 1004 等信封错误）：解信封抛业务码
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
  let done: TutorDone | null = null

  /** 解析一个 SSE 事件块（event: 行 + data: 行；默认事件为 delta 增量）。 */
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
      done = { sessionId: payload.sessionId, suggestions: payload.suggestions ?? [] }
    } else if (event === 'error') {
      throw new ApiError(payload.code ?? 2001, payload.message || '辅导回复生成失败')
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

/* ───────────── 即时辅导·流式回答（B-1，复用上面的 SSE 能力 + mock 兜底） ───────────── */

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** mock / 兜底回答：围绕用户问题与知识点确定性合成一段「先思路后清单」的辅导话术。 */
function mockInstantAnswer(message: string, kpName: string): string {
  const q = (message || '').trim().replace(/\s+/g, ' ')
  const focus = q ? (q.length > 36 ? `${q.slice(0, 36)}…` : q) : `${kpName}的核心思路`
  return (
    `我们一起看「${kpName}」这块。你提到「${focus}」——别急，这是个很常见的卡点。\n\n` +
    `先抓住三件事：\n` +
    `① 它要解决的核心问题是什么；\n` +
    `② 输入经过哪几步变换得到输出；\n` +
    `③ 它和相邻概念是怎么衔接的。\n\n` +
    `顺着这个思路，我可以为你生成更直观的图解 / 例题 / 短视频 / 讲义片段。下面这份针对性清单挑你需要的生成即可 👇`
  )
}

export interface InstantReplyOptions {
  kpId: string
  message: string
  kpName?: string
  /** 逐字增量回调（联调走真实 SSE delta，mock 走本地逐字） */
  onDelta: (delta: string) => void
}

/**
 * 即时辅导回答（逐字流式）。
 * - 联调：复用 8.7 苏格拉底 `tutorChatStream` 真实 SSE；中途失败但已出字则保留片段不重复兜底。
 * - mock / 无后端：本地逐字吐出确定性话术，体感与流式一致。
 * `control.cancelled` 置 true 可随时中止（关闭抽屉 / 发起新问题时）。
 */
export async function streamInstantReply(
  opts: InstantReplyOptions,
  control: { cancelled: boolean } = { cancelled: false }
): Promise<void> {
  const emit = (d: string) => {
    if (!control.cancelled) opts.onDelta(d)
  }

  // 文档上下文（kpId 形如 `doc:<id>`）：8.7 苏格拉底 chat 仅认知识点，直接走本地话术，
  // 避免对 /resource/tutor/chat 发起必然 404 的请求（针对性资源生成走文档专属链路）。
  const isDocContext = !!opts.kpId && opts.kpId.startsWith('doc:')

  if (USE_REAL_API && !isDocContext) {
    let started = false
    // 超时看门狗：整体上限 + 「首字节/长时间无新增量」空闲上限，超时即中止底层 fetch，
    // 避免后端 SSE 挂起导致回答永久停在「正在思考…」（即时辅导卡死）。
    const ac = new AbortController()
    const OVERALL_MS = 25_000
    const IDLE_MS = 9_000
    let idleTimer: ReturnType<typeof setTimeout> | null = null
    const overallTimer = setTimeout(() => ac.abort(), OVERALL_MS)
    const bumpIdle = () => {
      if (idleTimer) clearTimeout(idleTimer)
      idleTimer = setTimeout(() => ac.abort(), IDLE_MS)
    }
    bumpIdle()
    try {
      await tutorChatStream(
        {
          kpId: opts.kpId,
          message: opts.message,
          onDelta: (d) => {
            // 抽屉关闭/发起新问题 → control.cancelled，立即中止读取，不再继续出字
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
      return
    } catch (e) {
      console.error('[instant-tutor] SSE 失败/超时，回退本地流式', e)
      if (started || control.cancelled) return // 已出字则保留片段；已取消则不再兜底
    } finally {
      clearTimeout(overallTimer)
      if (idleTimer) clearTimeout(idleTimer)
    }
  }

  // mock / 兜底：逐字吐出
  const text = mockInstantAnswer(opts.message, opts.kpName ?? '当前知识点')
  for (const ch of text) {
    if (control.cancelled) return
    emit(ch)
    await sleep(15)
  }
}
