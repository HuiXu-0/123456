import { useState, useRef } from 'react'
import { USE_REAL_API } from '../services/api'
import { tutorChatStream } from '../services/tutor'
import { getResourceKpId } from '../services/resourceNav'
import { suggestResources, type RemedialSuggestResult } from '../services/tutorResource'
import { kpById } from '../data/knowledgePoints'
import { ksPointById } from '../data/knowledgeSystem'
import { socraticScriptFor } from '../data/socraticScripts'
import TutorResourcePanel from './TutorResourcePanel'
import ChatPanel, { type ChatMsg as Msg } from './ChatPanel'

/* 苏格拉底式导学：用引导式提问而非直接喂答案。
   开场白 / 引导链 / 快捷 chips 全部按「当前知识点」从 socraticScripts 取脚本
   （核心点精修引导链，体系点通用引导序列），学 AI Agent 就问 Agent、学 CNN 就问 CNN。
   规则驱动的演示版；联调时 SSE 携带同一 kpId 走真实 Agent 流式回复。*/
export default function SocraticTutor() {
  const kpId = getResourceKpId()
  const ksPoint = ksPointById(kpId)
  const kpName = kpById(kpId)?.name ?? ksPoint?.name ?? '当前知识点'
  const scriptRef = useRef(socraticScriptFor(kpId, kpName, ksPoint?.description))
  const script = scriptRef.current

  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'agent', text: script.opening }])
  const [input, setInput] = useState('')
  const [typing, setTyping] = useState(false)

  /* 通用脚本按轮次推进引导序列；精修脚本命中关键词分支，未命中走兜底追问 */
  const stageRef = useRef(0)
  const reply = (userText: string) => {
    const hit = script.branches?.find((b) => b.match.test(userText))
    if (hit) return hit.reply
    if (script.sequence && stageRef.current < script.sequence.length) {
      return script.sequence[stageRef.current++]
    }
    return script.fallback
  }

  /* B7-b 联调：8.7/15.4 SSE 流式（逐 delta 追加既有气泡，done 后刷新 suggestions chips）。
     首个 delta 前沿用打字三点动画；SSE 失败回退本地引导链（已渲染片段保留） */
  const sendLive = async (text: string) => {
    let acc = ''
    let started = false
    const appendDelta = (delta: string) => {
      acc += delta
      if (!started) {
        started = true
        setTyping(false)
        setMsgs((m) => [...m, { role: 'agent', text: acc }])
      } else {
        setMsgs((m) => [...m.slice(0, -1), { role: 'agent', text: acc }])
      }
    }
    try {
      const done = await tutorChatStream({
        kpId: getResourceKpId(),
        sessionId: sessionRef.current,
        message: text,
        onDelta: appendDelta,
      })
      sessionRef.current = done.sessionId
      if (done.suggestions.length) setChips(done.suggestions)
      setTyping(false)
      // 空流（无任何 delta）按失败处理，走本地引导链兜底
      if (!started) setMsgs((m) => [...m, { role: 'agent', text: reply(text) }])
    } catch (e) {
      console.error('[tutor] SSE 流式失败，回退本地引导链', e)
      setTyping(false)
      if (!started) setMsgs((m) => [...m, { role: 'agent', text: reply(text) }])
    }
  }

  /* 智能辅导·按需资源生成（8.8）：识别问题点 → 资源生成清单（学生勾选按需生成） */
  const [lastUser, setLastUser] = useState('')
  const [suggestion, setSuggestion] = useState<RemedialSuggestResult | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const askForHelp = async () => {
    if (suggesting) return
    setSuggesting(true)
    const question = lastUser || '我没懂这个知识点，需要更直观的针对性资源'
    try {
      setSuggestion(await suggestResources(kpId, question, kpName))
    } catch (e) {
      console.error('[tutor] 资源建议失败', e)
    } finally {
      setSuggesting(false)
    }
  }

  const send = () => {
    const text = input.trim()
    if (!text || typing) return
    setLastUser(text)
    setMsgs((m) => [...m, { role: 'user', text }])
    setInput('')
    setTyping(true)
    if (USE_REAL_API) {
      void sendLive(text)
      return
    }
    const answer = reply(text)
    window.setTimeout(() => {
      setTyping(false)
      setMsgs((m) => [...m, { role: 'agent', text: answer }])
    }, 700)
  }

  /* 联调：done 事件返回的 suggestions 覆盖快捷 chips（mock 模式恒为当前知识点脚本的 chips） */
  const [chips, setChips] = useState<string[]>(script.chips)
  const sessionRef = useRef<string | undefined>(undefined)

  return (
    <div className="socratic-wrap">
      <ChatPanel
        avatar="🦉"
        title="苏格拉底导学助手"
        subtitle="引导式提问 · 不直接给答案，帮你自己想通"
        msgs={msgs}
        typing={typing}
        chips={chips}
        onChip={setInput}
        input={input}
        onInput={setInput}
        onSend={send}
        placeholder="说说你的想法，我来引导你…"
        sending={typing}
      />

      {/* 智能辅导触发：卡住时一键识别问题点并给出针对性资源生成清单（8.8） */}
      <div className="socratic-help">
        <button className="socratic-help__btn" onClick={askForHelp} disabled={suggesting}>
          {suggesting ? '正在识别问题点…' : '💡 即时辅导 · 生成针对性资源'}
        </button>
        {suggestion && <span className="socratic-help__hint">已根据你的问题识别盲区 ↓</span>}
      </div>

      {suggestion && <TutorResourcePanel suggestion={suggestion} />}
    </div>
  )
}
