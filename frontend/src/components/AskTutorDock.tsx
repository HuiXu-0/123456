import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import TutorResourcePanel from './TutorResourcePanel'
import { suggestResources, type RemedialSuggestResult, type RemedialResource } from '../services/tutorResource'
import { streamInstantReply } from '../services/tutor'
import { onOpenTutor, openResourceModal } from '../services/tutorBus'
import type { ExternalResource } from './ResourceAggregator'
import './AskTutorDock.css'

/**
 * 即时辅导 · 常驻辅导入口（核心功能升格）。
 *
 * 解决目标用户「自学卡住没人答疑」的核心痛点：在学习界面右侧常驻一个醒目入口，
 * 学到卡住随手就能问。点击 → 描述/识别问题点 → AI 逐字流式回答 → 针对性资源清单 → 勾选按需生成。
 *
 * 完全复用既有链路与接口，不改后端：
 *  - streamInstantReply() = 8.7 苏格拉底 SSE 流式（逐字回答，mock 本地逐字兜底）
 *  - suggestResources() = POST /resource/tutor/suggest（识别问题点 + 资源清单）
 *  - TutorResourcePanel 内部 generateResources() = POST /resource/tutor/generate（勾选逐项生成）
 *
 * 外部入口（选中讲义提问 B-2 / 主动察觉卡顿 B-3）经 tutorBus 命令本组件打开并自动发起一次提问。
 */
interface Props {
  kpId: string
  kpName: string
}

/* 常见卡点的引导问句（点一下填入输入框，降低「不知道怎么问」的门槛）*/
const QUICK_QUESTIONS = [
  '这个知识点的核心思路没看懂',
  '公式/推导这一段卡住了',
  '想要一个直观的例子',
]

export default function AskTutorDock({ kpId, kpName }: Props) {
  const [open, setOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('') // B-1：逐字流式回答
  const [answering, setAnswering] = useState(false) // 回答流式进行中
  const [suggesting, setSuggesting] = useState(false)
  const [suggestion, setSuggestion] = useState<RemedialSuggestResult | null>(null)
  const controlRef = useRef<{ cancelled: boolean } | null>(null)

  /* 一次完整提问：先逐字流式回答（B-1），结束后再识别问题点出资源清单。
     设计要点（修复「即时辅导一直卡住/不稳定」）：
     1) 不再用 `if (answering||suggesting) return` 静默丢弃新提问——而是「取消上一条 + 重启本条」，
        让选中提问可随时重试、与抽屉内直接提问表现一致；
     2) 凡因取消而提前 return 的路径，状态(answering/suggesting)由「接管者」负责重置：
        新提问会自行置位，关闭抽屉由 close() 显式清零——避免回调 return 后 busy 永久为 true、
        重开抽屉时输入框/按钮被 disabled 卡死。 */
  const ask = async (q: string) => {
    // 取消上一条仍在流的回答/在途的资源建议（避免并发串扰）
    if (controlRef.current) controlRef.current.cancelled = true
    const control = { cancelled: false }
    controlRef.current = control

    setQuestion(q)
    setAnswer('')
    setSuggestion(null)
    setSuggesting(false)
    setAnswering(true)
    try {
      await streamInstantReply(
        { kpId, message: q, kpName, onDelta: (d) => { if (!control.cancelled) setAnswer((prev) => prev + d) } },
        control
      )
    } catch (e) {
      console.error('[instant-tutor] 流式回答失败', e)
    }
    if (control.cancelled) return // 已被新提问/关闭接管，状态由接管者重置
    setAnswering(false)

    // 回答收尾后再出资源清单
    setSuggesting(true)
    try {
      const res = await suggestResources(kpId, q, kpName)
      if (!control.cancelled) {
        setSuggestion(res)
        // 不再直接弹窗；由 TutorResourcePanel 供用户勾选后逐项生成，生成完毕回调 onGenerated 再弹窗
      }
    } catch (e) {
      console.error('[instant-tutor] 资源建议失败', e)
    } finally {
      if (!control.cancelled) setSuggesting(false)
    }
  }

  /* 主提问：用输入内容识别问题点；留空则回退为「就当前讲义辅导」 */
  const submit = () =>
    void ask(question.trim() || `我在学习「${kpName}」时卡住了，需要更直观的针对性资源`)

  /* 兜底增强：针对当前讲义内容直接发起辅导（无需输入；选中即问见 SelectionAskBubble） */
  const askCurrent = () =>
    void ask(`针对「${kpName}」当前讲义内容，我还没完全理解，帮我做更针对性的拆解`)

  const close = () => {
    // 关闭即中止在途回答/建议，并清零忙碌态——否则下次打开抽屉时 busy 仍为 true，
    // 输入框与按钮被 disabled，表现为「即时辅导一直卡住」。
    if (controlRef.current) {
      controlRef.current.cancelled = true
      controlRef.current = null
    }
    setAnswering(false)
    setSuggesting(false)
    setOpen(false)
  }

  /* 外部入口（选中提问 / 主动关怀）经 tutorBus 打开抽屉并按需自动发起提问 */
  const askRef = useRef(ask)
  askRef.current = ask
  useEffect(
    () =>
      onOpenTutor(({ question: q, autoAsk }) => {
        setOpen(true)
        if (q != null) setQuestion(q)
        if (autoAsk && q) void askRef.current(q)
      }),
    []
  )

  /* 卸载时中止可能在流的回答 */
  useEffect(() => () => {
    if (controlRef.current) controlRef.current.cancelled = true
  }, [])

  const busy = answering || suggesting

  /** TutorResourcePanel 生成完毕回调：组装完整资源 → 弹窗展示 */
  const handleGenerated = (result: {
    remedial: Partial<Record<string, RemedialResource>>
    external: ExternalResource[] | null
  }) => {
    const resources: Array<{
      type: string; title: string; mermaid?: string; markdown?: string;
      statement?: string; solution?: string;
      scenes?: Array<{ title: string; points: string[]; narration: string }>;
      items?: ExternalResource[];
    }> = []
    for (const [type, r] of Object.entries(result.remedial)) {
      if (!r) continue
      resources.push({ type, title: r.title, mermaid: r.mermaid, markdown: r.markdown,
        statement: r.statement, solution: r.solution, scenes: r.scenes })
    }
    if (result.external?.length) {
      resources.push({ type: 'external', title: `资源推荐 · ${suggestion?.problemPoint ?? ''}`, items: result.external })
    }
    openResourceModal({ resources, kpName, problemPoint: suggestion?.problemPoint ?? '' })
  }

  return (
    <>
      {/* 常驻侧边入口：主按钮级权重，但置于学习区右缘工具位，不与「去通关」主 CTA 抢焦点 */}
      <button
        type="button"
        className={`ask-tutor-dock ${open ? 'ask-tutor-dock--hidden' : ''}`}
        onClick={() => setOpen(true)}
        aria-label="打开即时辅导"
      >
        <span className="ask-tutor-dock__icon" aria-hidden>💡</span>
        <span className="ask-tutor-dock__label">即时辅导</span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="ask-tutor-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={close}
            role="dialog"
            aria-modal="true"
            aria-label="即时辅导"
          >
            <motion.aside
              className="ask-tutor-drawer"
              initial={{ x: 40, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 40, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 260, damping: 30 }}
              onClick={(e) => e.stopPropagation()}
            >
              <header className="ask-tutor-drawer__head">
                <div className="ask-tutor-drawer__title">
                  <span className="ask-tutor-drawer__icon" aria-hidden>💡</span>
                  <div>
                    <h3>即时辅导 · 针对性答疑</h3>
                    <p className="ask-tutor-drawer__sub">
                      针对<strong>「{kpName}」</strong>· 卡住随手就能问
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  className="ask-tutor-drawer__close"
                  onClick={close}
                  aria-label="关闭"
                >
                  ×
                </button>
              </header>

              <div className="ask-tutor-drawer__body">
                <p className="ask-tutor-drawer__lead">
                  自学卡住没人答疑？描述你哪里没懂，AI 立刻逐字解答并生成针对性资源（图解 / 例题 / 视频 / 讲义）。
                </p>

                <textarea
                  className="ask-tutor-drawer__input"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder={`例如：我不理解「${kpName}」里的某一步是怎么推出来的…`}
                  rows={3}
                  disabled={busy}
                />

                <div className="ask-tutor-drawer__chips">
                  {QUICK_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      type="button"
                      className="ask-tutor-drawer__chip"
                      onClick={() => setQuestion(q)}
                      disabled={busy}
                    >
                      {q}
                    </button>
                  ))}
                </div>

                <div className="ask-tutor-drawer__actions">
                  <button
                    type="button"
                    className="ask-tutor-drawer__primary"
                    onClick={submit}
                    disabled={busy}
                  >
                    {answering
                      ? '正在逐字解答…'
                      : suggesting
                        ? '正在识别问题点…'
                        : '识别问题 → 推荐针对性资源'}
                  </button>
                  <button
                    type="button"
                    className="ask-tutor-drawer__ghost"
                    onClick={askCurrent}
                    disabled={busy}
                    title="无需输入，直接就当前讲义内容发起辅导（或在讲义里选中一句「就这段问 AI」）"
                  >
                    📖 针对当前讲义辅导
                  </button>
                </div>

                {/* B-1：AI 逐字流式回答（出字前显示「正在思考」态） */}
                {(answering || answer) && (
                  <div className="ask-tutor-answer">
                    <span className="ask-tutor-answer__avatar" aria-hidden>🦉</span>
                    <div className="ask-tutor-answer__bubble">
                      {answer ? (
                        <span className="ask-tutor-answer__text">
                          {answer}
                          {answering && <span className="ask-tutor-answer__caret" aria-hidden />}
                        </span>
                      ) : (
                        <span className="ask-tutor-answer__thinking" aria-label="正在思考">
                          <i />
                          <i />
                          <i />
                          <em>正在思考…</em>
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {/* 回答收尾后展示资源生成面板（勾选 → 生成 → 弹窗） */}
                {suggestion && (
                  <TutorResourcePanel
                    suggestion={suggestion}
                    kpName={kpName}
                    onGenerated={handleGenerated}
                  />
                )}
              </div>
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
