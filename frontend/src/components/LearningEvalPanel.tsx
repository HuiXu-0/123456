import { motion } from 'framer-motion'
import type { LearningEvaluation, EvalTrend } from '../services/learningEval'
import type { PageType } from '../App'
import './LearningEvalPanel.css'

/**
 * 学习评估面板（接口文档 12.2，C-fix 批3）。
 * 在学情概览展示「学习评估 Agent」产出的多维评估 + 学习方法建议 + 动态调整建议。
 * 数据由真实做题/进度行为派生（联调），mock 模式由掌握度合成（同结构）。
 */

const TREND_META: Record<EvalTrend, { label: string; cls: string; icon: string }> = {
  improving: { label: '上升', cls: 'is-up', icon: '↗' },
  declining: { label: '下滑', cls: 'is-down', icon: '↘' },
  stable: { label: '平稳', cls: 'is-flat', icon: '→' },
}

interface Props {
  evaluation: LearningEvaluation
  onNavigate?: (page: PageType) => void
}

export default function LearningEvalPanel({ evaluation: e, onNavigate }: Props) {
  const trend = TREND_META[e.trend]
  return (
    <motion.section
      className="lev"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <header className="lev__head">
        <div className="lev__title-wrap">
          <span className="lev__badge">✦ 学习评估</span>
          <h3 className="lev__title">学习过程评估</h3>
          <span className="lev__by">{e.generatedBy === 'mock' ? '本地合成' : `${e.generatedBy} 生成`}</span>
        </div>
        <div className="lev__overall">
          <span className="lev__overall-score">{e.overallScore}</span>
          <span className="lev__overall-meta">
            <span className="lev__level">{e.level}</span>
            <span className={`lev__trend ${trend.cls}`}>{trend.icon} 趋势{trend.label}</span>
          </span>
        </div>
      </header>

      <p className="lev__summary">{e.summary}</p>

      {/* 多维指标 */}
      <div className="lev__dims">
        {e.dimensions.map((d) => (
          <div className="lev__dim" key={d.key}>
            <div className="lev__dim-head">
              <span className="lev__dim-label">{d.label}</span>
              <span className="lev__dim-score">{d.score}</span>
            </div>
            <div className="lev__meter">
              <span
                className={`lev__meter-fill ${d.score >= 75 ? 'is-strong' : d.score >= 45 ? 'is-mid' : 'is-weak'}`}
                style={{ width: `${d.score}%` }}
              />
            </div>
            <span className="lev__dim-detail">{d.detail}</span>
          </div>
        ))}
      </div>

      <div className="lev__cols">
        {/* 学习方法建议 */}
        <div className="lev__col">
          <h4 className="lev__col-title">学习方法建议</h4>
          <ul className="lev__suggestions">
            {e.suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
          {e.weakPoints.length > 0 && (
            <div className="lev__weak">
              <span className="lev__weak-label">薄弱点</span>
              {e.weakPoints.map((w) => (
                <span className="lev__weak-chip" key={w.kpId} title={w.reason}>
                  {w.name}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* 动态调整建议 */}
        <div className="lev__col lev__col--adjust">
          <h4 className="lev__col-title">动态调整建议</h4>
          <p className="lev__adjust-action">{e.adjustment.action}</p>
          <p className="lev__adjust-diff">📐 {e.adjustment.difficultyAdvice}</p>
          {e.adjustment.nextKpName && (
            <button
              type="button"
              className="lev__adjust-cta"
              onClick={() => onNavigate?.('learning-resource')}
            >
              去学「{e.adjustment.nextKpName}」 →
            </button>
          )}
        </div>
      </div>
    </motion.section>
  )
}
