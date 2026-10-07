import { motion } from 'framer-motion'
import './AgentStatusCard.css'

interface AgentStatusCardProps {
  name: string
  status: 'idle' | 'running' | 'success' | 'error'
  lastAction: string
}

export default function AgentStatusCard({ name, status, lastAction }: AgentStatusCardProps) {
  const statusConfig = {
    idle: { label: '待命', color: 'idle', icon: '◻' },
    running: { label: '运行中', color: 'running', icon: '◉' },
    success: { label: '完成', color: 'success', icon: '●' },
    error: { label: '错误', color: 'error', icon: '✕' }
  }

  const config = statusConfig[status]

  return (
    <motion.div
      className="agent-status-card"
      whileHover={{ scale: 1.01, y: -2 }}
      transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
    >
      {/* 状态指示灯 */}
      <div className={`agent-status-card__light agent-status-card__light--${config.color}`}>
        <span className="agent-status-card__light-dot" />
        {status === 'running' && (
          <>
            <span className="agent-status-card__ping" />
            <span className="agent-status-card__ping agent-status-card__ping--delay" />
          </>
        )}
      </div>

      {/* Agent信息 */}
      <div className="agent-status-card__content">
        <span className="agent-status-card__name">{name}</span>
        <span className="agent-status-card__action">{lastAction}</span>
      </div>

      {/* 状态徽章 */}
      <span className={`agent-status-card__badge agent-status-card__badge--${config.color}`}>
        {config.label}
      </span>
    </motion.div>
  )
}
