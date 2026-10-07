import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { exportLectureMarkdown } from '../utils/lectureExport'
import type { FlashcardCard } from '../services/documentLearning'
import './FlashcardDeck.css'

/**
 * 闪卡翻卡组件（文档学习新做）：正/背面 3D 翻转 + 整套翻页浏览。
 *
 * - 点击卡片 / 空格 / 回车翻面；← → 翻页；圆点跳转任意卡。
 * - 复用既有下载能力（exportLectureMarkdown）导出整套闪卡为 Markdown（正=问、背=答）。
 * - 纯设计令牌 + framer-motion，深浅主题自适应。
 */
export default function FlashcardDeck({
  cards,
  title = '闪卡',
  downloadName,
}: {
  cards: FlashcardCard[]
  title?: string
  downloadName?: string
}) {
  const [idx, setIdx] = useState(0)
  const [flipped, setFlipped] = useState(false)
  /* 翻页方向（+1 向后 / -1 向前）：驱动卡片进出场滑动方向 */
  const [dir, setDir] = useState(1)

  const total = cards.length
  const go = useCallback(
    (next: number) => {
      if (total === 0) return
      const clamped = (next + total) % total
      setDir(next > idx || (idx === total - 1 && clamped === 0) ? 1 : -1)
      setIdx(clamped)
      setFlipped(false) // 翻页回到正面，避免露答案
    },
    [idx, total]
  )

  const prev = useCallback(() => go(idx - 1), [go, idx])
  const next = useCallback(() => go(idx + 1), [go, idx])
  const flip = useCallback(() => setFlipped((f) => !f), [])

  /* 键盘：← → 翻页，空格 / 回车翻面 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        prev()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        next()
      } else if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault()
        flip()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [prev, next, flip])

  const handleDownload = () => {
    if (!total) return
    const md = [
      `# ${title}`,
      '',
      `> 共 ${total} 张闪卡，由 AI 基于文档生成。`,
      '',
      ...cards.flatMap((c, i) => [`## 卡片 ${i + 1}`, '', `**正面（问）**：${c.front}`, '', `**背面（答）**：${c.back}`, '']),
    ].join('\n')
    exportLectureMarkdown(md, downloadName ?? `闪卡-${title}`)
  }

  if (total === 0) {
    return <div className="resource-loading">暂无闪卡内容。</div>
  }

  const card = cards[idx]

  return (
    <div className="flashcard-deck">
      <div className="flashcard-deck__bar">
        <span className="flashcard-deck__counter">
          <span className="flashcard-deck__counter-cur">{idx + 1}</span>
          <span className="flashcard-deck__counter-sep">/</span>
          {total}
        </span>
        <div className="flashcard-deck__progress" aria-hidden="true">
          <span className="flashcard-deck__progress-fill" style={{ width: `${((idx + 1) / total) * 100}%` }} />
        </div>
        <button
          type="button"
          className="flashcard-deck__dl"
          onClick={handleDownload}
          title="下载整套闪卡（Markdown）"
        >
          <span aria-hidden="true">⬇</span> 下载闪卡
        </button>
      </div>

      <div className="flashcard-deck__stage">
        <button
          type="button"
          className="flashcard-deck__nav flashcard-deck__nav--prev"
          onClick={prev}
          aria-label="上一张"
        >
          ‹
        </button>

        <div className="flashcard-deck__viewport">
          <AnimatePresence mode="wait" custom={dir}>
            <motion.button
              key={idx}
              type="button"
              className={`flashcard-card ${flipped ? 'flashcard-card--flipped' : ''}`}
              onClick={flip}
              custom={dir}
              initial={{ opacity: 0, x: dir * 48, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: dir * -48, scale: 0.96 }}
              transition={{ duration: 0.28, ease: [0.25, 0.8, 0.25, 1] }}
              aria-label={`闪卡 ${idx + 1}，点击翻面`}
            >
              <span className="flashcard-card__inner">
                <span className="flashcard-card__face flashcard-card__face--front">
                  <span className="flashcard-card__tag">问</span>
                  <span className="flashcard-card__text">{card.front}</span>
                  <span className="flashcard-card__hint">点击查看答案</span>
                </span>
                <span className="flashcard-card__face flashcard-card__face--back">
                  <span className="flashcard-card__tag flashcard-card__tag--back">答</span>
                  <span className="flashcard-card__text">{card.back}</span>
                  <span className="flashcard-card__hint">点击返回问题</span>
                </span>
              </span>
            </motion.button>
          </AnimatePresence>
        </div>

        <button
          type="button"
          className="flashcard-deck__nav flashcard-deck__nav--next"
          onClick={next}
          aria-label="下一张"
        >
          ›
        </button>
      </div>

      <div className="flashcard-deck__controls">
        <button type="button" className="flashcard-deck__btn" onClick={prev}>
          上一张
        </button>
        <button type="button" className="flashcard-deck__btn flashcard-deck__btn--flip" onClick={flip}>
          {flipped ? '看问题' : '翻面看答案'}
        </button>
        <button type="button" className="flashcard-deck__btn" onClick={next}>
          下一张
        </button>
      </div>

      {total <= 24 && (
        <div className="flashcard-deck__dots">
          {cards.map((_, i) => (
            <button
              key={i}
              type="button"
              className={`flashcard-deck__dot ${i === idx ? 'flashcard-deck__dot--active' : ''}`}
              onClick={() => go(i)}
              aria-label={`跳到第 ${i + 1} 张`}
            />
          ))}
        </div>
      )}
    </div>
  )
}
