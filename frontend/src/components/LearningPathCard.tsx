import { motion } from 'framer-motion'
import './LearningPathCard.css'

interface LearningPathCardProps {
  sequence: number
  topic: string
  difficulty: string
  status: 'completed' | 'in_progress' | 'pending'
  progress: number
  /** 会话三·时间线：该步预计时长（分钟） */
  estimatedMinutes?: number
  /** 点击卡片 / 底部按钮的动作（进入该知识点学习）。缺省保持纯展示。 */
  onOpen?: () => void
}

export default function LearningPathCard({ sequence, topic, difficulty, status, progress, estimatedMinutes, onOpen }: LearningPathCardProps) {
  const difficultyClass = {
    '入门': 'beginner',
    '初级': 'elementary',
    '中级': 'intermediate',
    '高级': 'advanced',
    '精通': 'expert'
  }[difficulty] || 'intermediate'

  const duration = estimatedMinutes && estimatedMinutes > 0
    ? (estimatedMinutes < 60 ? `约 ${estimatedMinutes} 分钟` : `约 ${Number.isInteger(estimatedMinutes / 60) ? estimatedMinutes / 60 : (estimatedMinutes / 60).toFixed(1)} 小时`)
    : ''

  return (
    <motion.div
      className={`learning-path-card learning-path-card--${status}${onOpen ? ' learning-path-card--clickable' : ''}`}
      whileHover={{ scale: 1.02, y: -4 }}
      transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
      onClick={onOpen}
    >
      {/* 流光连接线 */}
      {status !== 'pending' && (
        <div className="learning-path-card__connector">
          <div className="learning-path-card__connector-line" />
          <div className="learning-path-card__connector-glow" />
        </div>
      )}

      {/* 序号节点 */}
      <div className="learning-path-card__node">
        <div className="learning-path-card__node-circle">
          <span>{sequence}</span>
        </div>
        {status === 'completed' && <div className="learning-path-card__node-ring" />}
        {status === 'in_progress' && <div className="learning-path-card__node-pulse" />}
      </div>

      {/* 内容 */}
      <div className="learning-path-card__content">
        <div className="learning-path-card__header">
          <h4 className="learning-path-card__topic">{topic}</h4>
          <span className={`difficulty-badge difficulty-badge--${difficultyClass}`}>
            {difficulty}
          </span>
        </div>

        {/* 会话三·时间线：预计学习时长 */}
        {duration && (
          <div className="learning-path-card__duration">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 2" />
            </svg>
            {duration}
          </div>
        )}

        {/* 状态 */}
        <div className="learning-path-card__status">
          {status === 'completed' && (
            <span className="learning-path-card__status-badge learning-path-card__status-badge--completed">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              已完成
            </span>
          )}
          {status === 'in_progress' && (
            <span className="learning-path-card__status-badge learning-path-card__status-badge--progress">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              进行中 {progress}%
            </span>
          )}
          {status === 'pending' && (
            <span className="learning-path-card__status-badge learning-path-card__status-badge--pending">
              待学习
            </span>
          )}
        </div>

        {/* 进度条 */}
        {status === 'in_progress' && (
          <div className="learning-path-card__progress">
            <div className="progress-bar">
              <div className="progress-bar__fill progress-bar__fill--animated" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {/* 玻璃按钮 */}
        <button
          className="learning-path-card__action"
          onClick={(e) => {
            e.stopPropagation()
            onOpen?.()
          }}
        >
          {status === 'completed' ? '复习巩固' : status === 'in_progress' ? '继续学习' : '开始学习'}
        </button>
      </div>
    </motion.div>
  )
}
