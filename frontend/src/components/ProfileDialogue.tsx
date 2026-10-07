import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import ChatPanel, { type ChatMsg } from './ChatPanel'
import StudentPortraitPanel from './StudentPortraitPanel'
import ProfileConfirmModal from './ProfileConfirmModal'
import {
  CANONICAL_DIMS,
  getStudentPortrait,
  profileDialogueStream,
  type DialogueInteraction,
  type PortraitDimension,
} from '../services/profileDialogue'
import { getJobMatch, type JobMatchResult } from '../services/jobMatch'
import { usePortrait } from '../store/portrait'
import { useMastery } from '../store/mastery'
import './ProfileDialogue.css'

/**
 * 对话式学习画像诊断主路径（接口文档 17.1 + 17.2/17.3）。
 * 左：复用 ChatPanel 对话 UI（与 SocraticTutor 同款气泡 / 打字动画 / chip / SSE 逐 token）；
 * 右：StudentPortraitPanel 随 event:portrait 增量实时刷新异质 6 维。
 * 摒弃繁琐表单——对话为主路径；表单 / 材料上传由父级降级为次要入口。
 */
const OPENING =
  '你好，我是你的学习画像助手 ✦。咱们用聊天的方式——我边问你边了解情况，右侧画像会随对话实时「长」出来，不用填表。先聊聊：你现在的专业或职业背景是什么？平时接触编程 / AI 多吗？'

const OPENING_CHIPS = ['计算机本科，会点 Python', '非科班，自学中', '有工作经验想转 AI']

interface Props {
  /** 诊断完成后的衔接：沿用既有 completeDiagnosis + 导航流程。
   *  jobInfo（C-fix 批2）：对话采集到目标岗位时算出的岗位匹配，打通学情概览目标岗位口径。 */
  onFinish: (jobInfo?: { targetJobName: string; matchPct: number }) => void
  context?: { major?: string; goal?: string }
}

export default function ProfileDialogue({ onFinish, context }: Props) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([{ role: 'agent', text: OPENING }])
  const [input, setInput] = useState('')
  const [typing, setTyping] = useState(false)
  const [sending, setSending] = useState(false)
  const [chips, setChips] = useState<string[]>(OPENING_CHIPS)
  const [complete, setComplete] = useState(false)

  /** 异质画像维度（按 key 索引，event:portrait 增量合并） */
  const [dims, setDims] = useState<Record<string, PortraitDimension>>({})
  const [portraitTs, setPortraitTs] = useState<string | undefined>(undefined)
  /** 当前待作答的引导式交互（微测题 / 偏好选择题），由 event:interaction 驱动 */
  const [interaction, setInteraction] = useState<DialogueInteraction | null>(null)
  const sessionRef = useRef<string | undefined>(undefined)

  /** 「学情概况确认」弹窗：诊断完成且满 6 维才弹（防止确认到不足维度的画像） */
  const [showConfirm, setShowConfirm] = useState(false)
  const setPortrait = usePortrait((s) => s.setPortrait)
  const masteryStatus = useMastery((s) => s.status)

  /** 岗位匹配（C-fix 批2）：对话采集到目标岗位后，用现有静态岗位库算匹配度 + 能力缺口 */
  const [jobMatch, setJobMatch] = useState<JobMatchResult | null>(null)

  const filledCount = CANONICAL_DIMS.filter((c) => dims[c.key]).length
  const allDimsFilled = filledCount >= CANONICAL_DIMS.length

  // 满 6 维 + diagnosisComplete 才自动弹确认弹窗（dims 异步落定后由本 effect 触发，避免时序竞态）
  useEffect(() => {
    if (complete && allDimsFilled) setShowConfirm(true)
  }, [complete, allDimsFilled])

  // 诊断收敛后据「学习目标」维度算岗位匹配（现有静态岗位库，不联网）；无目标岗位则不展示
  useEffect(() => {
    if (!(complete && allDimsFilled)) return
    const goal = dims['learning_goal']?.value ?? ''
    if (!goal) {
      setJobMatch(null)
      return
    }
    const masteredKps = Object.entries(masteryStatus)
      .filter(([, v]) => v === 'passed')
      .map(([k]) => k)
    let cancelled = false
    getJobMatch(goal, { masteredKps })
      .then((r) => { if (!cancelled) setJobMatch(r) })
      .catch((e) => console.error('[profile-dialogue] 岗位匹配失败', e))
    return () => { cancelled = true }
  }, [complete, allDimsFilled, dims, masteryStatus])

  // 确认画像：落快照（学情概览据此合成）→ 走既有完成流程（completeDiagnosis + 解锁学习路径）；
  // 带上岗位匹配结果，使学情概览的目标岗位/匹配度有真实出处、口径一致。
  const handleConfirm = () => {
    setPortrait(Object.values(dims), portraitTs)
    setShowConfirm(false)
    onFinish(jobMatch ? { targetJobName: jobMatch.jobName, matchPct: jobMatch.matchPct } : undefined)
  }

  // 进页拉取当前画像（联调时可能已有历史维度；mock 返回空画像）
  useEffect(() => {
    let cancelled = false
    getStudentPortrait()
      .then((p) => {
        if (cancelled) return
        if (p.dimensions.length) {
          setDims(Object.fromEntries(p.dimensions.map((d) => [d.key, d])))
          setPortraitTs(p.updatedAt)
        }
      })
      .catch((e) => console.error('[profile-dialogue] 拉取画像失败', e))
    return () => {
      cancelled = true
    }
  }, [])

  const applyPortrait = (updates: PortraitDimension[]) => {
    if (!updates.length) return
    setDims((prev) => {
      const next = { ...prev }
      for (const u of updates) next[u.key] = u
      return next
    })
    setPortraitTs(updates[updates.length - 1].updatedAt ?? new Date().toISOString())
  }

  /* 17.1 SSE：逐 delta 追加既有 agent 气泡；event:portrait 实时刷右栏；event:interaction 抛出
     待作答微测/偏好卡片；done 后刷新 chips + 完成态。首个 delta 前沿用打字三点动画；失败保留
     已渲染片段与已抽取维度（17.1 约定）。displayText 入用户气泡；answer 携带点选项稳定值。 */
  const runTurn = async (displayText: string, answer?: string) => {
    if (sending) return
    setMsgs((m) => [...m, { role: 'user', text: displayText }])
    setInteraction(null) // 提交后即收起卡片，避免重复作答
    setTyping(true)
    setSending(true)

    let acc = ''
    let started = false
    const appendDelta = (delta: string) => {
      acc += delta
      if (!started) {
        started = true
        setTyping(false)
        setMsgs((m) => [...m, { role: 'agent', text: acc }])
      } else {
        setMsgs((m) => {
          const last = m[m.length - 1]
          return last && last.role === 'agent'
            ? [...m.slice(0, -1), { role: 'agent', text: acc }]
            : [...m, { role: 'agent', text: acc }]
        })
      }
    }

    try {
      const done = await profileDialogueStream({
        sessionId: sessionRef.current,
        message: displayText,
        answer,
        context: sessionRef.current ? undefined : context, // 仅首轮带冷启动 context
        onDelta: appendDelta,
        onPortrait: applyPortrait,
        onInteraction: setInteraction,
      })
      sessionRef.current = done.sessionId
      if (done.suggestions.length) setChips(done.suggestions)
      if (done.diagnosisComplete) setComplete(true)
      if (!started) setMsgs((m) => [...m, { role: 'agent', text: '（已记录你的回答）' }])
    } catch (e) {
      console.error('[profile-dialogue] SSE 失败', e)
      if (!started) setMsgs((m) => [...m, { role: 'agent', text: '抱歉，我刚才没接上话，要不你再说一次？' }])
    } finally {
      setTyping(false)
      setSending(false)
    }
  }

  const send = () => {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    void runTurn(text)
  }

  // 点选微测/偏好选项：用户气泡显示选项标签，answer 回传稳定值（option_id / optionKey）
  const pickOption = (opt: { value: string; label: string }) => void runTurn(opt.label, opt.value)

  return (
    <div className="pd">
      <div className="pd__chat profile-builder__card">
        <ChatPanel
          avatar="🎓"
          title="学习画像助手"
          subtitle="对话采集 · 微测真实能力 · 偏好归类型"
          msgs={msgs}
          typing={typing}
          chips={interaction ? [] : chips}
          onChip={(q) => (q.startsWith('生成') ? setShowConfirm(true) : setInput(q))}
          input={input}
          onInput={setInput}
          onSend={send}
          placeholder={interaction ? '点选下方选项，或直接输入…' : '用自己的话说说你的情况…'}
          sending={sending}
          /* 微测/偏好作答卡常驻「对话区 ↔ 输入框」之间：回答（上方气泡）与待答题目分区不重叠 */
          belowChat={
            <AnimatePresence>
              {interaction && (
                <motion.div
                  className={`pd-itx pd-itx--${interaction.type}`}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                >
                  <div className="pd-itx__head">
                    <span className="pd-itx__tag">
                      {interaction.type === 'quiz' ? '诊断微测 · 行为反推能力' : '偏好选择 · 只归类型不打分'}
                    </span>
                    {interaction.meta?.kpName && <span className="pd-itx__kp">{interaction.meta.kpName}</span>}
                  </div>
                  <div className="pd-itx__prompt">{interaction.prompt}</div>
                  <div className="pd-itx__opts">
                    {interaction.options.map((o) => (
                      <button
                        key={o.value}
                        className="pd-itx__opt"
                        disabled={sending}
                        onClick={() => pickOption(o)}
                      >
                        <span className="pd-itx__opt-label">{o.label}</span>
                        {o.hint && <span className="pd-itx__opt-hint">{o.hint}</span>}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          }
        />
      </div>

      <aside className="pd__aside">
        {/* 完成态（内嵌按钮）同样以「满 6 维」为门槛，点击打开确认弹窗而非直接跳转 */}
        <StudentPortraitPanel
          dims={dims}
          updatedAt={portraitTs}
          complete={complete && allDimsFilled}
          onFinish={() => setShowConfirm(true)}
        />
      </aside>

      <ProfileConfirmModal
        open={showConfirm}
        dims={dims}
        updatedAt={portraitTs}
        onConfirm={handleConfirm}
        onClose={() => setShowConfirm(false)}
      />
    </div>
  )
}
