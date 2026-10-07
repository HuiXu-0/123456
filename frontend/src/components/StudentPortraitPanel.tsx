import { AnimatePresence, motion } from 'framer-motion'
import { CANONICAL_DIMS, type PortraitDimension } from '../services/profileDialogue'
import ProvenanceBadge from './ProvenanceBadge'

/**
 * 对话式诊断页右侧「动态学习画像」面板（接口 38，17.2；C2 三分类呈现）。
 *
 * C2 关键修复——**能力打分、偏好归类型、主观描述，分区呈现、不再混进同一 0-100 轴**：
 *   - 能力维（ability）：进度条 + 分数 + 「依据 basis」（行为微测反推，可解释、防臆造）；
 *   - 偏好维（preference）：类型标签 + 图标（🖼 图像型 · 稳步细钻 · 易概念混淆），**无分数**；
 *   - 主观维（subjective）：自然语言描述（学习目标 / 先验经验），不强行打分。
 *
 * 与 Dashboard 的 4.4 知识点能力雷达并行——二者用途不同（此处为诊断过程的实时画像）。
 */
interface Props {
  dims: Record<string, PortraitDimension>
  updatedAt?: string
  complete: boolean
  onFinish: () => void
}

const SOURCE_META: Record<PortraitDimension['source'], { label: string; cls: string }> = {
  dialogue: { label: '对话采集', cls: 'dialogue' },
  inferred: { label: '推断', cls: 'inferred' },
  manual: { label: '手动填写', cls: 'manual' },
  diagnostic: { label: '微测实测', cls: 'diagnostic' },
  self_report: { label: '自述解析', cls: 'self_report' },
  default: { label: '默认零基础', cls: 'default' },
}

// 偏好类型码 → 图标（仅呈现，类型本身无高低之分）
const PREF_ICON: Record<string, string> = {
  visual: '🖼', textual: '📖', example: '🧩',
  overview: '⚡', deepdive: '🧗',
  concept: '🌀', calculation: '🔢', coding: '💻',
}

function confLevel(c: number): 'high' | 'mid' | 'low' {
  if (c >= 0.75) return 'high'
  if (c >= 0.6) return 'mid'
  return 'low'
}

function fmtTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const ABILITY = CANONICAL_DIMS.filter((c) => c.kind === 'ability')
const PREFERENCE = CANONICAL_DIMS.filter((c) => c.kind === 'preference')
const SUBJECTIVE = CANONICAL_DIMS.filter((c) => c.kind === 'subjective')

export default function StudentPortraitPanel({ dims, updatedAt, complete, onFinish }: Props) {
  const filled = CANONICAL_DIMS.filter((d) => dims[d.key]).length
  const total = CANONICAL_DIMS.length
  const pct = Math.round((filled / total) * 100)
  const has = (keys: { key: string }[]) => keys.some((c) => dims[c.key])

  return (
    <div className="sp-panel">
      <div className="sp-panel__head">
        <div>
          <div className="sp-panel__title">
            <span className="sp-panel__spark">✦</span> 动态学习画像
          </div>
          <div className="sp-panel__sub">能力靠测 · 偏好归类型 · 主观靠对话</div>
        </div>
        <div className="sp-panel__count">
          <span className="sp-panel__count-num">
            {filled}
            <span className="sp-panel__count-total">/{total}</span>
          </span>
          <span className="sp-panel__count-label">维度已采集</span>
        </div>
      </div>

      {/* 画像来源/置信度（任务 3）：让用户清楚这份画像有多可信 */}
      {filled > 0 && (
        <div className="sp-panel__prov">
          <ProvenanceBadge dims={Object.values(dims)} showHint />
        </div>
      )}

      <div className="sp-panel__progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <motion.span
          className="sp-panel__progress-fill"
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={{ type: 'spring', stiffness: 120, damping: 20 }}
        />
      </div>

      {/* ① 能力画像：打分 + 依据（行为微测反推，可解释） */}
      <section className="sp-sec">
        <div className="sp-sec__title sp-sec__title--ability">能力画像 · 测出来的</div>
        {ABILITY.map((c) => {
          const dim = dims[c.key]
          return (
            <AnimatePresence mode="wait" key={c.key}>
              {dim ? (
                <motion.div key="f" className="sp-ability" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
                  <div className="sp-ability__top">
                    <span className="sp-ability__label">{dim.label}</span>
                    <span className={`sp-srctag sp-srctag--${SOURCE_META[dim.source].cls}`}>{SOURCE_META[dim.source].label}</span>
                  </div>
                  {typeof dim.score === 'number' ? (
                    <div className="sp-ability__score">
                      <div className="sp-ability__bar">
                        <motion.span className="sp-ability__bar-fill" initial={{ width: 0 }} animate={{ width: `${dim.score}%` }} transition={{ duration: 0.6, ease: 'easeOut' }} />
                      </div>
                      <span className="sp-ability__num">{dim.score}</span>
                    </div>
                  ) : (
                    <div className="sp-ability__untested">未测 · 低置信（诊断微测未作答，不臆造分数）</div>
                  )}
                  <div className="sp-ability__verdict">{dim.value}</div>
                  {dim.basis && <div className="sp-ability__basis">依据：{dim.basis}</div>}
                  <div className="sp-card__foot">
                    <span className={`sp-conf sp-conf--${confLevel(dim.confidence)}`}>
                      <span className="sp-conf__dot" />
                      置信 {Math.round(dim.confidence * 100)}%
                    </span>
                    {fmtTime(dim.updatedAt) && <span className="sp-card__time">{fmtTime(dim.updatedAt)}</span>}
                  </div>
                </motion.div>
              ) : (
                <motion.div key="g" className="sp-ability sp-ability--ghost" initial={false}>
                  <span className="sp-ability__label">{c.label}</span>
                  <span className="sp-card__pending">待微测</span>
                </motion.div>
              )}
            </AnimatePresence>
          )
        })}
      </section>

      {/* ② 偏好画像：类型标签（无分数、不上 0-100 轴） */}
      <section className="sp-sec">
        <div className="sp-sec__title sp-sec__title--pref">偏好画像 · 归类型不打分</div>
        <div className="sp-pref">
          {PREFERENCE.map((c) => {
            const dim = dims[c.key]
            return dim ? (
              <motion.span key={c.key} className="sp-pref__tag" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}>
                <span className="sp-pref__icon">{(dim.optionKey && PREF_ICON[dim.optionKey]) || '◆'}</span>
                <span className="sp-pref__type">{dim.value}</span>
                <span className="sp-pref__dim">{dim.label}</span>
              </motion.span>
            ) : (
              <span key={c.key} className="sp-pref__tag sp-pref__tag--ghost">
                <span className="sp-pref__icon">◇</span>
                <span className="sp-pref__type">待选择</span>
                <span className="sp-pref__dim">{c.label}</span>
              </span>
            )
          })}
        </div>
      </section>

      {/* ③ 主观信息：对话采集的描述（不打分） */}
      <section className="sp-sec">
        <div className="sp-sec__title sp-sec__title--subj">主观信息 · 对话采集</div>
        {SUBJECTIVE.map((c) => {
          const dim = dims[c.key]
          return (
            <div className="sp-subj" key={c.key}>
              <span className="sp-subj__label">{dim?.label ?? c.label}</span>
              <span className={`sp-subj__value ${dim ? '' : 'sp-subj__value--ghost'}`}>{dim ? dim.value : '待对话采集'}</span>
            </div>
          )
        })}
      </section>

      <AnimatePresence>
        {complete && (
          <motion.div className="sp-done" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <div className="sp-done__text">
              <strong>诊断完成</strong>
              <span>能力已测出、偏好已归类，可据此生成针对性学习路径</span>
            </div>
            <button className="sp-done__btn" onClick={onFinish}>
              生成针对性学习路径 →
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <p className="sp-panel__note">
        能力画像由「诊断微测」按答题行为反推、带依据，不靠自陈；偏好画像只归类型、不打分，
        不混进 0-100 能力轴；空作答标「未测/低置信」，绝不臆造。
        {has(PREFERENCE) && updatedAt ? ` 更新于 ${fmtTime(updatedAt)}` : ''}
      </p>
    </div>
  )
}
