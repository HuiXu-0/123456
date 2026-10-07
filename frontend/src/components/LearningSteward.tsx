/**
 * 学情管家（Learning Steward）——统领性展示层。
 *
 * 由现有能力收拢而成，分两块：
 * - StewardHero：管家身份叙事 + 全局学情监测指标条（综合分/掌握/覆盖/已产出）。
 * - AgentActivityBoard：各专家 Agent 活动汇总（管家看板）+ 主动建议。
 *
 * 组件本身无副作用、不发请求：数据由 Dashboard 从既有 store/service 派生后注入。
 */
import type { PageType } from '../App'
import type { AgentActivity, StewardSummary } from '../services/stewardActivity'
import './LearningSteward.css'

/* ---------------- 管家身份 + 全局监测 ---------------- */

export interface StewardHeroProps {
  userName: string
  overallLevel: string
  overallScore: number
  masteredCore: number
  totalCore: number
  coveragePct: number
  resourceCount: number
  activeAgents: number
  totalAgents: number
  /** 监测时刻文本（如 HH:mm），由父组件传入避免组件内计时 */
  monitoredAt: string
}

export function StewardHero(props: StewardHeroProps) {
  const {
    userName,
    overallLevel,
    overallScore,
    masteredCore,
    totalCore,
    coveragePct,
    resourceCount,
    activeAgents,
    totalAgents,
    monitoredAt,
  } = props

  const metrics = [
    { label: '综合能力', value: `${overallScore}`, unit: '分' },
    { label: '知识点掌握', value: `${masteredCore}/${totalCore}`, unit: '' },
    { label: '图谱覆盖', value: `${coveragePct}`, unit: '%' },
    { label: '已产出资源', value: `${resourceCount}`, unit: '份' },
  ]

  return (
    <div className="steward-hero">
      <div className="steward-hero__glow" aria-hidden="true" />

      <div className="steward-hero__lead">
        <div className="steward-hero__orb" aria-hidden="true">
          <span className="steward-hero__orb-ring" />
          <span className="steward-hero__orb-ring steward-hero__orb-ring--2" />
          <svg viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
            <circle cx="12" cy="9" r="3.2" fill="currentColor" opacity="0.9" />
            <path d="M6 19c0-3 2.7-5 6-5s6 2 6 5" fill="currentColor" opacity="0.55" />
          </svg>
        </div>

        <div className="steward-hero__intro">
          <div className="steward-hero__titlerow">
            <h2 className="steward-hero__title">AI 学情管家</h2>
            <span className="steward-hero__live">
              <span className="steward-hero__live-dot" />
              监测中 · {monitoredAt}
            </span>
          </div>
          <p className="steward-hero__desc">
            你好，{userName}。我持续监测你的学习全过程，正协调
            <strong> 诊断 / 规划 / 生成 / 评估 / 文档学习 </strong>
            {totalAgents} 位专家 Agent 为你服务——当前处于 <strong>{overallLevel}</strong> 水平，
            {activeAgents}/{totalAgents} 位专家已产出成果。
          </p>
        </div>
      </div>

      <div className="steward-hero__metrics">
        {metrics.map((m) => (
          <div key={m.label} className="steward-metric">
            <span className="steward-metric__value">
              {m.value}
              {m.unit && <span className="steward-metric__unit">{m.unit}</span>}
            </span>
            <span className="steward-metric__label">{m.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------- 各 Agent 活动汇总 + 主动建议 ---------------- */

const STATUS_LABEL: Record<AgentActivity['status'], string> = {
  done: '已产出',
  active: '进行中',
  idle: '待触发',
}

const TONE_ICON: Record<string, string> = {
  primary: '➜',
  warn: '⚠',
  info: '✦',
}

export interface AgentActivityBoardProps {
  summary: StewardSummary
  onNavigate?: (page: PageType) => void
}

export interface AgentActivityPanelProps extends AgentActivityBoardProps {
  /** 附加到卡片容器的布局类（如 dashboard__dual__main） */
  className?: string
}

/** 各 Agent 活动汇总（管家看板）单卡：中枢首页「学习监测」段与组合看板共用。 */
export function AgentActivityPanel({ summary, onNavigate, className }: AgentActivityPanelProps) {
  return (
    <div className={`dashboard__card${className ? ` ${className}` : ''}`}>
      <div className="card-header">
        <h3>各 Agent 活动汇总</h3>
        <span className="card-badge">
          {summary.activeAgents}/{summary.totalAgents} 已产出
        </span>
      </div>
      <div className="card-content">
        <div className="steward-agents">
          {summary.agents.map((a) => (
            <button
              key={a.key}
              type="button"
              className={`steward-agent steward-agent--${a.status}`}
              onClick={() => onNavigate?.(a.page)}
              title={`前往：${a.role}`}
            >
              <span className={`steward-agent__status steward-agent__status--${a.status}`}>
                <span className="steward-agent__dot" />
                {STATUS_LABEL[a.status]}
              </span>
              <span className="steward-agent__body">
                <span className="steward-agent__name">{a.name}</span>
                <span className="steward-agent__action">{a.lastAction}</span>
              </span>
              <span className="steward-agent__metric">
                <span className="steward-agent__metric-num">{a.metric}</span>
                <span className="steward-agent__metric-label">{a.metricLabel}</span>
              </span>
              <span className="steward-agent__chevron" aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

export interface StewardSuggestPanelProps extends AgentActivityBoardProps {
  className?: string
}

/** 管家主动建议单卡：中枢首页「管家建议」段与组合看板共用。 */
export function StewardSuggestPanel({ summary, onNavigate, className }: StewardSuggestPanelProps) {
  return (
    <div className={`dashboard__card${className ? ` ${className}` : ''}`}>
      <div className="card-header">
        <h3>主动建议</h3>
        <span className="card-badge">管家</span>
      </div>
      <div className="card-content">
        <div className="steward-suggests">
          {summary.suggestions.map((s) => (
            <div key={s.id} className={`steward-suggest steward-suggest--${s.tone}`}>
              <span className="steward-suggest__icon" aria-hidden="true">
                {TONE_ICON[s.tone] ?? '✦'}
              </span>
              <div className="steward-suggest__body">
                <p className="steward-suggest__text">{s.text}</p>
                {s.cta && s.page && (
                  <button
                    type="button"
                    className="steward-suggest__cta"
                    onClick={() => onNavigate?.(s.page!)}
                  >
                    {s.cta} →
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/** 组合看板（既有签名保持不变）：左 Agent 活动 + 右主动建议。 */
export function AgentActivityBoard({ summary, onNavigate }: AgentActivityBoardProps) {
  return (
    <div className="dashboard__dual steward-board">
      <AgentActivityPanel summary={summary} onNavigate={onNavigate} className="dashboard__dual__main" />
      <StewardSuggestPanel summary={summary} onNavigate={onNavigate} className="dashboard__dual__side" />
    </div>
  )
}
