import { useRef, useState } from 'react'
import { kpById } from '../data/knowledgePoints'
import { ksPointById } from '../data/knowledgeSystem'
import { feynmanStream, type FeynmanGap, type ReviewRef } from '../services/learningFlow'
import ChatPanel, { type ChatMsg as Msg } from './ChatPanel'

/**
 * 费曼讲解法（以讲代学）：学习者用自己的话「讲解」知识点，系统流式点评 + 追问，
 * 末尾给出知识缺口 gaps[]，并用 review[] 链接引导回看对应资源。
 * 复用 ChatPanel 对话 UI（与苏格拉底辅导同体例），接 18.2 SSE：
 *   onDelta 逐片段改写末条气泡 · onGaps 汇总缺口 · done 用 followups 刷新快捷 chips。
 */
interface FeynmanTutorProps {
  kpId: string
  /** 点击缺口的「回看」链接：把对应资源带回学习内容/资源中枢。 */
  onReview?: (ref: ReviewRef) => void
  /** 讲解充分（complete=true）回调：父级据此置 18.3 feynman=true。 */
  onComplete?: () => void
}

const SEVERITY_LABEL: Record<FeynmanGap['severity'], string> = {
  high: '关键缺口',
  medium: '待补充',
  low: '小提示',
}

const REVIEW_KIND_LABEL: Record<string, string> = {
  lecture: '讲义',
  diagram: '图解',
  video: '视频',
  quiz: '测试',
  mindmap: '导图',
  code: '实操',
}

export default function FeynmanTutor({ kpId, onReview, onComplete }: FeynmanTutorProps) {
  const kpName = kpById(kpId)?.name ?? ksPointById(kpId)?.name ?? '这个知识点'
  const opening = `轮到你当老师了 🎓。请用你自己的话，把「${kpName}」讲给一个完全没学过的人听——讲得越具体越好。我会顺着你的讲解点评、追问，并帮你找出还没讲清的地方。`

  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'agent', text: opening }])
  const [input, setInput] = useState('')
  const [typing, setTyping] = useState(false)
  const [gaps, setGaps] = useState<FeynmanGap[]>([])
  /* 讲解引子 chips 跟随当前知识点（nn 保留精修引子，其余点用 kpName 锚定的通用引子），
     与苏格拉底导学同口径：不再对任意知识点都显示神经网络的引子。 */
  const [chips, setChips] = useState<string[]>(() =>
    kpId === 'nn'
      ? ['它就是把输入加权求和…', '然后经过激活函数输出…', '靠反向传播更新权重…']
      : [`「${kpName}」要解决的问题是…`, '它的核心原理是…', '举个实际例子…'],
  )
  const sessionRef = useRef<string | undefined>(undefined)

  const send = () => {
    const text = input.trim()
    if (!text || typing) return
    setMsgs((m) => [...m, { role: 'user', text }])
    setInput('')
    setTyping(true)

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

    feynmanStream({
      kpId,
      sessionId: sessionRef.current,
      explanation: text,
      onDelta: appendDelta,
      onGaps: (g) => setGaps(g),
    })
      .then((done) => {
        sessionRef.current = done.sessionId
        setTyping(false)
        if (done.followups.length) setChips(done.followups)
        if (!started) {
          // 空流兜底：给一条提示，避免无回应
          setMsgs((m) => [...m, { role: 'agent', text: '我没太接住，换种说法再讲一遍？' }])
        }
        if (done.complete) {
          setGaps([])
          onComplete?.()
        }
      })
      .catch((e) => {
        console.error('[feynman] SSE 失败', e)
        setTyping(false)
        if (!started) {
          setMsgs((m) => [
            ...m,
            { role: 'agent', text: '评估服务暂时不可用，先把你的讲解保存在脑子里，稍后再试 🙏' },
          ])
        }
      })
  }

  return (
    <div className="feynman">
      <div className="feynman__chat">
        <ChatPanel
          avatar="🎓"
          title="费曼讲解 · 以讲代学"
          subtitle="把知识讲出来，暴露并补上理解漏洞"
          msgs={msgs}
          typing={typing}
          chips={chips}
          onChip={setInput}
          input={input}
          onInput={setInput}
          onSend={send}
          placeholder="像讲给别人听一样，把你的理解说出来…"
          sendLabel="提交讲解"
          sending={typing}
        />
      </div>

      <aside className="feynman__gaps">
        <div className="feynman__gaps-head">
          <span className="feynman__gaps-title">🔍 待回看清单</span>
          <span className="feynman__gaps-count">{gaps.length}</span>
        </div>
        {gaps.length === 0 ? (
          <p className="feynman__gaps-empty">
            讲解后，这里会列出你还没讲清的知识缺口，并给出回看资源的直达链接。
          </p>
        ) : (
          <ul className="feynman__gap-list">
            {gaps.map((g) => (
              <li key={g.id} className={`feynman__gap feynman__gap--${g.severity}`}>
                <div className="feynman__gap-head">
                  <span className="feynman__gap-sev">{SEVERITY_LABEL[g.severity]}</span>
                  <span className="feynman__gap-title">{g.title}</span>
                </div>
                <p className="feynman__gap-detail">{g.detail}</p>
                {g.review && g.review.length > 0 && (
                  <div className="feynman__gap-reviews">
                    {g.review.map((r, i) => (
                      <button
                        key={`${g.id}-${i}`}
                        type="button"
                        className="feynman__review"
                        onClick={() => onReview?.(r)}
                      >
                        ↩ 回看{REVIEW_KIND_LABEL[r.kind] ?? r.kind}：{r.title}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  )
}
