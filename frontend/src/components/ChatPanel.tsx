import { useRef, useEffect, type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import './ChatPanel.css'

/**
 * 通用对话面板（从 SocraticTutor 抽取的可复用对话 UI）。
 * 承载站内统一的「气泡 + 打字三点动画 + 快捷回复 chip + 输入区」呈现，纯展示、状态由父级持有。
 * 消费方：①8.7 苏格拉底辅导（SocraticTutor）②17.1 对话式画像诊断（ProfileDialogue），
 * 二者共用同一 `.socratic*` 设计令牌与 SSE 增量渲染节奏（父级逐 delta 改写末条 agent 气泡）。
 */
export interface ChatMsg {
  role: 'agent' | 'user'
  text: string
}

interface ChatPanelProps {
  avatar: string
  title: string
  subtitle: string
  msgs: ChatMsg[]
  /** 首个 delta 到达前的「正在输入」三点动画 */
  typing: boolean
  chips: string[]
  onChip: (text: string) => void
  input: string
  onInput: (value: string) => void
  onSend: () => void
  placeholder?: string
  sendLabel?: string
  /** 流式进行中：仅禁用发送（输入仍可继续打字） */
  sending?: boolean
  /** 会话锁定（如诊断已完成）：输入 / chips / 发送全部禁用 */
  locked?: boolean
  /** 对话区与输入区之间的常驻插槽（如画像诊断的微测/偏好作答卡，始终可见、不被遮挡） */
  belowChat?: ReactNode
  /** 无消息时占据对话区的空状态插槽（如文档学习的大文档卡）；不传则维持原样 */
  emptySlot?: ReactNode
  className?: string
}

export default function ChatPanel({
  avatar,
  title,
  subtitle,
  msgs,
  typing,
  chips,
  onChip,
  input,
  onInput,
  onSend,
  placeholder = '说说你的想法…',
  sendLabel = '发送',
  sending = false,
  locked = false,
  belowChat,
  emptySlot,
  className = '',
}: ChatPanelProps) {
  const chatRef = useRef<HTMLDivElement>(null)

  /* 新消息自动滚到底：只滚对话容器自身，不再用 scrollIntoView 牵动外层主滚动容器
     （避免整页跳动 / 输入框被顶出视口）。 */
  useEffect(() => {
    const el = chatRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs, typing])

  return (
    <div className={`socratic ${className}`}>
      <div className="socratic__head">
        <span className="socratic__avatar">{avatar}</span>
        <div>
          <div className="socratic__title">{title}</div>
          <div className="socratic__sub">{subtitle}</div>
        </div>
      </div>

      <div className="socratic__chat" ref={chatRef}>
        {msgs.length === 0 && !typing && emptySlot}
        <AnimatePresence initial={false}>
          {msgs.map((m, i) => (
            <motion.div
              key={i}
              className={`socratic__msg socratic__msg--${m.role}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
            >
              {m.role === 'agent' && <span className="socratic__msg-avatar">{avatar}</span>}
              <span className="socratic__bubble">{m.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
        {typing && (
          <div className="socratic__msg socratic__msg--agent">
            <span className="socratic__msg-avatar">{avatar}</span>
            <span className="socratic__bubble socratic__bubble--typing">
              <i />
              <i />
              <i />
            </span>
          </div>
        )}
      </div>

      {belowChat && <div className="socratic__slot">{belowChat}</div>}

      <div className="socratic__quick">
        {chips.map((q) => (
          <button key={q} className="socratic__chip" onClick={() => onChip(q)} disabled={locked}>
            {q}
          </button>
        ))}
      </div>

      <div className="socratic__input">
        <input
          value={input}
          placeholder={placeholder}
          disabled={locked}
          onChange={(e) => onInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSend()}
        />
        <button onClick={onSend} disabled={locked || sending || !input.trim()}>
          {sendLabel}
        </button>
      </div>
    </div>
  )
}
