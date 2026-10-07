import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { onStuck, STUCK_THRESHOLD } from '../services/stuckSignal'
import { openInstantTutor } from '../services/tutorBus'
import './InstantTutorExtras.css'

/**
 * 主动察觉卡顿（B-3）：行为数据累计到卡住阈值时，AI 主动弹一张轻量关怀小卡。
 * 点击「帮我讲讲」即进入即时辅导（带当前知识点上下文）。
 *
 * 限频是关键——同一知识点 / 同一会话最多弹 1 次；首次出现即写入 sessionStorage，
 * 于是「关闭后不再弹」「再多信号也不再打扰」一并满足。提示为右下角小卡，不打断当前操作。
 */
interface Props {
  kpId: string
  kpName: string
}

const seenKey = (kpId: string) => `instant-tutor-nudge:${kpId}`

export default function StuckNudge({ kpId, kpName }: Props) {
  const [show, setShow] = useState(false)

  useEffect(
    () =>
      onStuck((kp, count) => {
        if (kp !== kpId || count < STUCK_THRESHOLD) return
        try {
          if (sessionStorage.getItem(seenKey(kpId))) return // 本会话本知识点已弹过 → 不再打扰
          sessionStorage.setItem(seenKey(kpId), '1')
        } catch {
          /* 隐私模式禁用 storage 时退化为「内存内本次渲染只弹一次」 */
        }
        setShow(true)
      }),
    [kpId]
  )

  const dismiss = () => setShow(false)
  const enter = () => {
    setShow(false)
    openInstantTutor({
      question: `我在学「${kpName}」时有点卡，帮我把这块讲明白一些`,
      autoAsk: true,
    })
  }

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          className="stuck-nudge"
          role="status"
          initial={{ opacity: 0, y: 20, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 20, scale: 0.96 }}
          transition={{ type: 'spring', stiffness: 280, damping: 26 }}
        >
          <button className="stuck-nudge__close" onClick={dismiss} aria-label="关闭">
            ×
          </button>
          <span className="stuck-nudge__icon" aria-hidden>
            🦉
          </span>
          <div className="stuck-nudge__body">
            <p className="stuck-nudge__title">这块是不是有点难？</p>
            <p className="stuck-nudge__text">
              「{kpName}」看起来卡住了，要我帮你讲讲吗？
            </p>
            <div className="stuck-nudge__actions">
              <button className="stuck-nudge__primary" onClick={enter}>
                帮我讲讲
              </button>
              <button className="stuck-nudge__ghost" onClick={dismiss}>
                先不用
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
