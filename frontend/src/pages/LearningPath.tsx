import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import LearningPathCard from '../components/LearningPathCard'
import PageHeader from '../components/PageHeader'
import { RevealGroup, RevealItem, revealItem } from '../components/Reveal'
import type { PageType } from '../App'
import { getLearningPath, type Lesson, type LearningPathData } from '../services/learning'
import { useJourney } from '../store/journey'
import { useMastery } from '../store/mastery'
import { usePortrait } from '../store/portrait'
import { setResourceNav } from '../services/resourceNav'
import './LearningPath.css'

const RECOMMEND_TAB: Record<string, string> = {
  lecture: 'lecture', mindmap: 'mindmap', diagram: 'diagram',
  video: 'video', quiz: 'quiz', external: 'external',
}

/** 分钟 → 人性化时长（会话三·时间线）：<60 显示分钟，否则显示小时（去除 .0）。 */
function fmtDuration(min?: number): string {
  if (!min || min <= 0) return ''
  if (min < 60) return `约 ${min} 分钟`
  const h = min / 60
  return `约 ${Number.isInteger(h) ? h : h.toFixed(1)} 小时`
}
/** 分钟 → 小时数（用于「已花时长」，去除 .0）。 */
function toHours(min: number): string {
  const h = Math.round((min / 60) * 10) / 10
  return `${Number.isInteger(h) ? h : h.toFixed(1)}`
}

export default function LearningPath({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  const [viewMode, setViewMode] = useState<'timeline' | 'cards'>('timeline')
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null)
  const generatePath = useJourney((s) => s.generatePath)
  const masteryStatus = useMastery((s) => s.status)
  const portraitTs = usePortrait((s) => s.updatedAt)

  /* C2：路径真由后端 planner 按画像规划（节点/顺序/每步推荐资源因画像而变）。
     画像变（portraitTs）或掌握度变（masteryStatus）→ 重新拉取 → 路径相应重算，不写死。 */
  const [data, setData] = useState<LearningPathData | null>(null)
  useEffect(() => {
    let cancelled = false
    getLearningPath()
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e) => console.error('[learning-path] 拉取路径失败', e))
    return () => { cancelled = true }
  }, [portraitTs, masteryStatus])

  const pathData: Lesson[] = data?.lessons ?? []
  const milestones = data?.milestones ?? []
  const narrative = data?.summary.narrative ?? ''
  const timeline = data?.summary.timeline

  const recommendedOf = (l?: Lesson) => l?.resources?.find((r) => r.recommended)

  /* 弹窗按钮 → 资源页：携带该课程 kpId（后端直给）；「查看资源」默认落该步按偏好推荐的资源 Tab。 */
  const openResource = (topic: string, mode: 'flow' | 'browse', entryTab?: string) => {
    const lesson = pathData.find((p) => p.topic === topic)
    const rec = recommendedOf(lesson)
    const tab = entryTab ?? (rec ? RECOMMEND_TAB[rec.kind] : undefined)
    setResourceNav(lesson?.kpId ?? '', mode, tab)
    onNavigate?.('learning-resource')
  }

  /* 当前应学节点 = 路径中第一个未完成的节点（已按个性化顺序排好，取其 kpId）。 */
  const currentNode = pathData.find((l) => l.status !== 'completed') ?? pathData[pathData.length - 1]
  const currentKpId = currentNode?.kpId
  /* 当前进度位置：第一个未完成节点即「你在这里 / 下一步」，用于时间线高亮。 */
  const currentSeq = currentNode && currentNode.status !== 'completed' ? currentNode.sequence : -1

  const completedCount = pathData.filter(p => p.status === 'completed').length
  const inProgressCount = pathData.filter(p => p.status === 'in_progress').length
  const overallProgress = pathData.length
    ? Math.round(pathData.reduce((sum, p) => sum + p.progress, 0) / pathData.length)
    : 0

  return (
    <div className="learning-path-page">
      {/* 统一标题区：锚条 + 高亮 + 状态徽章组 */}
      <PageHeader
        title="个性化学习路径"
        highlight="学习路径"
        subtitle="基于您的学情诊断，为您量身定制的学习路线"
        crumb="学习路径"
        onBack={() => onNavigate?.('dashboard')}
        badges={[
          { label: '已完成', value: completedCount },
          { label: '进行中', value: inProgressCount },
          { label: '总进度', value: `${overallProgress}%` },
        ]}
      />

      {/* C2：为你这样规划的理由（个性化可感知、可解释） */}
      {narrative && (
        <motion.div className="path-rationale" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <span className="path-rationale__icon">✦</span>
          <span className="path-rationale__text">{narrative}</span>
        </motion.div>
      )}

      {/* 会话三·时间线：整条路径预计周期 / 总时长 / 进度 / 学习节奏建议 */}
      {timeline && timeline.totalCount > 0 && (
        <motion.div className="path-timeline" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}>
          <div className="path-timeline__head">
            <div className="path-timeline__cycle">
              <span className="path-timeline__cycle-num">预计 {timeline.cycleWeeks} 周</span>
              <span className="path-timeline__cycle-sub">学完全程</span>
            </div>
            <div className="path-timeline__facts">
              <span className="path-timeline__fact"><b>{timeline.totalCount}</b> 个知识点</span>
              <span className="path-timeline__dot">·</span>
              <span className="path-timeline__fact">共约 <b>{timeline.totalHours}</b> 小时</span>
              <span className="path-timeline__dot">·</span>
              <span className="path-timeline__fact">已学 <b>{timeline.learnedCount}/{timeline.totalCount}</b></span>
            </div>
          </div>
          <div className="path-timeline__progress">
            <div className="path-timeline__bar">
              <motion.div
                className="path-timeline__fill"
                initial={{ width: 0 }}
                animate={{ width: `${timeline.completionPct}%` }}
                transition={{ duration: 0.9, ease: 'easeOut' }}
              />
            </div>
            <span className="path-timeline__pct">{timeline.completionPct}%</span>
          </div>
          <div className="path-timeline__foot">
            <span className="path-timeline__spent">已投入约 {toHours(timeline.spentMinutes)} / {timeline.totalHours} 小时</span>
            <span className="path-timeline__pacing">⏱ {timeline.pacing}</span>
          </div>
        </motion.div>
      )}

      {/* View Toggle */}
      <div className="view-toggle">
        <button
          className={`view-toggle__btn ${viewMode === 'timeline' ? 'view-toggle__btn--active' : ''}`}
          onClick={() => setViewMode('timeline')}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="2" x2="12" y2="22" />
            <circle cx="12" cy="6" r="3" />
            <circle cx="12" cy="12" r="3" />
            <circle cx="12" cy="18" r="3" />
          </svg>
          时间线视图
        </button>
        <button
          className={`view-toggle__btn ${viewMode === 'cards' ? 'view-toggle__btn--active' : ''}`}
          onClick={() => setViewMode('cards')}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="9" rx="1" />
            <rect x="14" y="3" width="7" height="9" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          卡片视图
        </button>
      </div>

      <div className="learning-path-content">
        <AnimatePresence mode="wait">
          {viewMode === 'timeline' ? (
            <motion.div
              key="timeline"
              className="timeline-view"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              {/* Timeline */}
              <RevealGroup className="timeline">
                {pathData.map((item) => (
                  <motion.div
                    key={item.sequence}
                    className={`timeline-item timeline-item--${item.status}${item.sequence === currentSeq ? ' timeline-item--current' : ''}`}
                    variants={revealItem}
                    onClick={() => setSelectedTopic(item.topic)}
                  >
                    <div className="timeline-item__marker">
                      {item.status === 'completed' ? (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : item.status === 'in_progress' ? (
                        <motion.div
                          className="timeline-item__pulse"
                          animate={{ scale: [1, 1.2, 1] }}
                          transition={{ duration: 1.5, repeat: Infinity }}
                        />
                      ) : (
                        <span>{item.sequence}</span>
                      )}
                    </div>
                    <div className="timeline-item__content">
                      {item.sequence === currentSeq && (
                        <span className="timeline-item__here">▶ 下一步 · 你在这里</span>
                      )}
                      <div className="timeline-item__header">
                        <h3 className="timeline-item__title">{item.topic}</h3>
                        {item.estimatedMinutes ? (
                          <span className="timeline-item__duration" title="预计学习时长（按层级/难度估算）">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                              <circle cx="12" cy="12" r="9" />
                              <path d="M12 7v5l3 2" />
                            </svg>
                            {fmtDuration(item.estimatedMinutes)}
                          </span>
                        ) : null}
                        <span className={`difficulty-badge difficulty-badge--${
                          item.difficulty === '入门' ? 'beginner' :
                          item.difficulty === '初级' ? 'elementary' :
                          item.difficulty === '中级' ? 'intermediate' :
                          item.difficulty === '高级' ? 'advanced' : 'expert'
                        }`}>
                          {item.difficulty}
                        </span>
                      </div>
                      {item.reason && (
                        <p className="timeline-item__reason">
                          <span className="timeline-item__reason-icon">✦</span>{item.reason}
                        </p>
                      )}
                      {recommendedOf(item) && (
                        <span className="timeline-item__recommend" title={recommendedOf(item)!.recommendReason}>
                          为你推荐：{recommendedOf(item)!.title}
                        </span>
                      )}
                      {item.description && <p className="timeline-item__desc">{item.description}</p>}
                      {item.status === 'in_progress' && (
                        <div className="timeline-item__progress">
                          <div className="progress-bar">
                            <motion.div
                              className="progress-bar__fill"
                              initial={{ width: 0 }}
                              animate={{ width: `${item.progress}%` }}
                              transition={{ duration: 0.8, delay: 0.5 }}
                            />
                          </div>
                          <span className="progress-text">{item.progress}%</span>
                        </div>
                      )}
                      <span className={`status-badge status-badge--${item.status}`}>
                        {item.status === 'completed' ? '已完成' : item.status === 'in_progress' ? '进行中' : '待学习'}
                      </span>
                    </div>
                  </motion.div>
                ))}
              </RevealGroup>

              {/* Milestones Sidebar */}
              <div className="milestones-panel">
                <h3>学习里程碑</h3>
                <div className="milestones-list">
                  {milestones.map((milestone, index) => (
                    <motion.div
                      key={milestone.id}
                      className={`milestone ${milestone.completed ? 'milestone--completed' : ''}`}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.15 }}
                    >
                      <div className="milestone__icon">
                        {milestone.completed ? (
                          <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
                          </svg>
                        ) : (
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="12" cy="12" r="10" />
                            <path d="M12 6v6l4 2" />
                          </svg>
                        )}
                      </div>
                      <div className="milestone__info">
                        <span className="milestone__title">{milestone.title}</span>
                        <span className="milestone__date">{milestone.date}</span>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="cards"
              className="cards-view"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <RevealGroup className="cards-grid">
                {pathData.map((item) => (
                  <RevealItem key={item.sequence}>
                    <LearningPathCard
                      sequence={item.sequence}
                      topic={item.topic}
                      difficulty={item.difficulty}
                      status={item.status}
                      progress={item.progress}
                      estimatedMinutes={item.estimatedMinutes}
                      onOpen={() => setSelectedTopic(item.topic)}
                    />
                  </RevealItem>
                ))}
              </RevealGroup>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* 主线衔接：路径已就绪 → 进入 ③ 学习资源 */}
      <div className="flow-next">
        <div className="flow-next__text">
          <span className="flow-next__step">下一步 · ③ 学习资源</span>
          <span className="flow-next__desc">
            {currentNode ? `进入「${currentNode.topic}」获取 AI 讲义与练习` : '进入当前进度节点获取 AI 讲义与练习'}
          </span>
        </div>
        <button
          className="flow-next__btn"
          type="button"
          onClick={() => {
            generatePath()
            const rec = recommendedOf(currentNode)
            setResourceNav(currentKpId ?? '', 'browse', rec ? RECOMMEND_TAB[rec.kind] : undefined)
            onNavigate?.('learning-resource')
          }}
        >
          去学习资源 →
        </button>
      </div>

      {/* Topic Detail Modal */}
      <AnimatePresence>
        {selectedTopic && (
          <motion.div
            className="topic-modal-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setSelectedTopic(null)}
          >
            <motion.div
              className="topic-modal"
              initial={{ opacity: 0, scale: 0.9, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9, y: 20 }}
              onClick={e => e.stopPropagation()}
            >
              <button className="topic-modal__close" onClick={() => setSelectedTopic(null)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
              <h2>{selectedTopic}</h2>
              {(() => {
                const l = pathData.find((p) => p.topic === selectedTopic)
                const rec = recommendedOf(l)
                return (
                  <>
                    {l?.reason && <p className="topic-modal__reason">✦ {l.reason}</p>}
                    {l?.description && <p>{l.description}</p>}
                    {rec && (
                      <p className="topic-modal__recommend">
                        为你默认推荐：<strong>{rec.title}</strong>
                        {rec.recommendReason && <span className="topic-modal__recommend-why">{rec.recommendReason}</span>}
                      </p>
                    )}
                  </>
                )
              })()}
              <div className="topic-modal__actions">
                <button
                  className="topic-modal__btn topic-modal__btn--primary"
                  onClick={() => openResource(selectedTopic, 'flow')}
                >
                  开始学习
                </button>
                <button
                  className="topic-modal__btn topic-modal__btn--secondary"
                  onClick={() => openResource(selectedTopic, 'browse', 'external')}
                >
                  查看资源
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}