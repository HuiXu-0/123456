/**
 * 文档对话全局状态（跨页面切换持久化）。
 *
 * 解决：用户发消息后切页面，组件卸载导致流式回答被取消、聊天记录丢失。
 * 方案：聊天记录 + 流式请求放 Zustand store，组件卸载不影响，回来直接看结果。
 */
import { create } from 'zustand'
import { streamDocChat } from '../services/documentChat'
import type { SourceRef } from '../components/SourceTrace'

export interface ChatMsg {
  role: 'user' | 'agent'
  text: string
}

interface DocChatState {
  /** 当前对话范围（docIds 拼接）→ 用于判断是否需要重置会话 */
  scopeKey: string
  /** 聊天记录 */
  msgs: ChatMsg[]
  /** 是否正在流式输出 */
  typing: boolean
  /** 是否正在发送（含等待首个 token） */
  sending: boolean
  /** 会话 ID（用于后端上下文连续） */
  sessionId: string | undefined
  /** 每轮回答的溯源 */
  answerSources: Record<number, SourceRef[]>
  /** 溯源是否展开 */
  srcOpen: boolean

  /** 切换文档范围时重置会话 */
  resetSession: (newScopeKey: string) => void

  /** 发送消息（流式，后台持续运行不依赖组件） */
  send: (text: string, docIds: string[]) => Promise<void>

  /** 设置溯源展开状态 */
  setSrcOpen: (open: boolean) => void
}

/** 活跃的流式请求控制器（scopeKey → control） */
const _controls: Record<string, { cancelled: boolean }> = {}

export const useDocChatStore = create<DocChatState>()((set, get) => ({
  scopeKey: '',
  msgs: [],
  typing: false,
  sending: false,
  sessionId: undefined,
  answerSources: {},
  srcOpen: false,

  resetSession: (newScopeKey: string) => {
    // 取消旧范围的流式请求
    const old = _controls[get().scopeKey]
    if (old) old.cancelled = true
    set({
      scopeKey: newScopeKey,
      msgs: [],
      typing: false,
      sending: false,
      sessionId: undefined,
      answerSources: {},
      srcOpen: false,
    })
  },

  send: async (text: string, docIds: string[]) => {
    const q = text.trim()
    if (!q || docIds.length === 0 || get().sending) return

    const control = { cancelled: false }
    const scopeKey = docIds.join(',')
    _controls[scopeKey] = control

    let agentIdx = -1
    set((s) => {
      agentIdx = s.msgs.length + 1
      return { msgs: [...s.msgs, { role: 'user', text: q }], typing: true, sending: true }
    })

    let acc = ''
    let started = false
    const appendDelta = (d: string) => {
      acc += d
      if (!started) {
        started = true
        set({ typing: false })
        set((s) => ({ msgs: [...s.msgs, { role: 'agent', text: acc }] }))
      } else {
        set((s) => ({ msgs: [...s.msgs.slice(0, -1), { role: 'agent', text: acc }] }))
      }
    }

    try {
      const state = get()
      const done = await streamDocChat(
        { documentIds: docIds, sessionId: state.sessionId, message: q, onDelta: appendDelta },
        control
      )
      if (control.cancelled) return
      if (done.sessionId) set({ sessionId: done.sessionId })
      if (done.sources.length && agentIdx >= 0) {
        set((s) => ({ answerSources: { ...s.answerSources, [agentIdx]: done.sources } }))
      }
    } catch (e) {
      console.error('[doc-chat] 回答失败', e)
      if (!started) {
        set((s) => ({ msgs: [...s.msgs, { role: 'agent', text: '抱歉，本次回答生成失败，请稍后重试。' }] }))
      }
    } finally {
      set({ typing: false, sending: false })
    }
  },

  setSrcOpen: (open) => set({ srcOpen: open }),
}))
