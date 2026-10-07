import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import './PageHeader.css'

export interface HeaderBadge {
  label: string
  value: ReactNode
  /** default=普通 / accent=难度药丸 / safe=已校验绿 */
  tone?: 'default' | 'accent' | 'safe'
  /** 可选：徽章前的小圆点颜色（如知识图谱掌握度类别色）*/
  dot?: string
}

interface PageHeaderProps {
  title: string
  /** 标题中需高亮为 primary 的关键词（单个或多个）*/
  highlight?: string | string[]
  subtitle?: string
  /** 标题后的小标签（如 Beta）*/
  tag?: ReactNode
  /** 右侧状态徽章组（可选）*/
  badges?: HeaderBadge[]
  /** 右侧操作区（可选，如启动/重置按钮）*/
  actions?: ReactNode
  /** 全局返回中枢：提供则在标题上方渲染「← 学情管家 / 当前功能」层级条（additive，不影响既有页面）*/
  onBack?: () => void
  /** 层级指示中当前功能名（缺省用 title）*/
  crumb?: string
}

/** 把标题中命中的关键词包成 primary 高亮 span */
function renderTitle(title: string, highlight?: string | string[]) {
  const keys = (Array.isArray(highlight) ? highlight : highlight ? [highlight] : []).filter(Boolean)
  if (!keys.length) return title
  const escaped = keys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp(`(${escaped.join('|')})`, 'g')
  return title.split(re).map((part, i) =>
    keys.includes(part) ? (
      <span key={i} className="page-header__hl">
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    )
  )
}

/** 五大主页面共用的标题区：左 锚条+高亮标题(+小标)+副标题，右 状态徽章组 + 操作区 */
export default function PageHeader({ title, highlight, subtitle, tag, badges, actions, onBack, crumb }: PageHeaderProps) {
  const hasAside = (badges && badges.length > 0) || actions

  return (
    <motion.header
      className="page-header"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
    >
      {onBack && (
        <nav className="page-header__crumbs" aria-label="返回学情管家">
          <button type="button" className="page-header__back" onClick={onBack}>
            <span aria-hidden="true">←</span> 学情管家
          </button>
          <span className="page-header__crumb-sep" aria-hidden="true">/</span>
          <span className="page-header__crumb">{crumb ?? title}</span>
        </nav>
      )}
      <div className="page-header__lead">
        <h1 className="page-header__title">
          <span className="page-header__bar" aria-hidden="true" />
          <span className="page-header__text">{renderTitle(title, highlight)}</span>
          {tag && <span className="page-header__tag">{tag}</span>}
        </h1>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>

      {hasAside && (
        <div className="page-header__aside">
          {badges && badges.length > 0 && (
            <div className="page-header__badges">
              {badges.map((b, i) => (
                <div key={i} className={`page-header__badge page-header__badge--${b.tone ?? 'default'}`}>
                  {b.dot && <span className="page-header__badge-dot" style={{ background: b.dot }} />}
                  <span className="page-header__badge-main">
                    <span className="page-header__badge-value">{b.value}</span>
                    <span className="page-header__badge-label">{b.label}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          {actions && <div className="page-header__actions">{actions}</div>}
        </div>
      )}
    </motion.header>
  )
}
