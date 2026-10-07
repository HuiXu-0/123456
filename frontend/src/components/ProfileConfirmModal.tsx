import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import RadarChart from './charts/RadarChart'
import StudentPortraitPanel from './StudentPortraitPanel'
import { synthesizeOverview } from '../services/dashboard'
import { getAbilityPortrait, type AbilityPortrait, type PortraitDimension } from '../services/profileDialogue'
import './ProfileConfirmModal.css'

/**
 * 「学情概况确认」弹窗（问题 5）：对话诊断满 6 维且 diagnosisComplete 后弹出。
 * 全部内容由「真实画像」合成——左：6 维异质画像（复用 StudentPortraitPanel）；
 * 右：知识雷达（6 维画像）+ 优势 / 盲区（synthesizeOverview）。
 * 用户点「确认画像」后才正式生成学情概览并解锁学习路径（onConfirm）。
 */
interface Props {
  open: boolean
  dims: Record<string, PortraitDimension>
  updatedAt?: string
  onConfirm: () => void
  onClose: () => void
}

export default function ProfileConfirmModal({ open, dims, updatedAt, onConfirm, onClose }: Props) {
  const list = Object.values(dims)
  // 能力雷达由 4.4（微测/quiz 驱动的 Mastery 分）取真值；诊断期间已写库，故确认时即为实测
  const [ability, setAbility] = useState<AbilityPortrait | undefined>(undefined)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    getAbilityPortrait()
      .then((a) => { if (!cancelled) setAbility(a) })
      .catch((e) => console.error('[profile-confirm] 能力雷达加载失败', e))
    return () => { cancelled = true }
  }, [open])
  const overview = synthesizeOverview(list, ability)

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="pcm-mask"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className="pcm"
            role="dialog"
            aria-modal="true"
            aria-label="学情概况确认"
            initial={{ opacity: 0, y: 24, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 240, damping: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="pcm__close" aria-label="关闭" onClick={onClose}>
              ×
            </button>

            <div className="pcm__head">
              <span className="pcm__badge">✦ 诊断完成</span>
              <h3 className="pcm__title">学情概况确认</h3>
              <p className="pcm__sub">
                以下由你本次诊断的真实画像合成。核对无误后点「确认画像」，将据此生成学情概览并解锁学习路径。
              </p>
            </div>

            <div className="pcm__body">
              {/* 左：6 维异质画像（复用诊断页右栏面板，隐藏其内嵌完成按钮） */}
              <div className="pcm__col pcm__col--portrait">
                <StudentPortraitPanel dims={dims} updatedAt={updatedAt} complete={false} onFinish={onConfirm} />
              </div>

              {/* 右：知识雷达 + 优势 / 盲区 */}
              <div className="pcm__col pcm__col--analysis">
                <div className="pcm__card">
                  <div className="pcm__card-head">
                    <h4>能力掌握雷达 · 测出来的</h4>
                    <span className="pcm__card-tag">综合 {overview.overall_score} · {overview.overall_level}</span>
                  </div>
                  <div className="pcm__radar">
                    <RadarChart data={overview.radar} target={overview.radar.values} targetName="当前能力" />
                  </div>
                </div>

                {/* 偏好画像：类型标签（无分数、不上雷达轴） */}
                {overview.preferences.length > 0 && (
                  <div className="pcm__card">
                    <div className="pcm__card-head">
                      <h4>学习偏好 · 归类型不打分</h4>
                    </div>
                    <div className="pcm__prefs">
                      {overview.preferences.map((p) => (
                        <span className="pcm__pref" key={p.key}>
                          <span className="pcm__pref-type">{p.value}</span>
                          <span className="pcm__pref-dim">{p.label}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="pcm__card">
                  <div className="pcm__tags">
                    <div className="pcm__tag-group">
                      <span className="pcm__tag-label pcm__tag-label--strong">
                        <span className="pcm__dot pcm__dot--strong" /> 优势维度
                      </span>
                      {overview.strong_topics.length ? (
                        overview.strong_topics.map((t) => (
                          <div className="pcm__item" key={t.name}>
                            <span className="pcm__item-name">{t.name}</span>
                            <div className="pcm__meter">
                              <span className="pcm__meter-fill pcm__meter-fill--strong" style={{ width: `${t.mastery}%` }} />
                            </div>
                            <span className="pcm__item-pct">{t.mastery}</span>
                          </div>
                        ))
                      ) : (
                        <p className="pcm__empty">暂无明显优势维度</p>
                      )}
                    </div>
                    <div className="pcm__tag-group">
                      <span className="pcm__tag-label pcm__tag-label--weak">
                        <span className="pcm__dot pcm__dot--weak" /> 待补盲区
                      </span>
                      {overview.weak_topics.length ? (
                        overview.weak_topics.map((t) => (
                          <div className="pcm__item" key={t.name}>
                            <span className="pcm__item-name">{t.name}</span>
                            <div className="pcm__meter">
                              <span className="pcm__meter-fill pcm__meter-fill--weak" style={{ width: `${t.mastery}%` }} />
                            </div>
                            <span className="pcm__item-pct">{t.mastery}</span>
                          </div>
                        ))
                      ) : (
                        <p className="pcm__empty">暂无明显盲区</p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="pcm__foot">
              <button className="pcm__btn pcm__btn--ghost" onClick={onClose}>
                再聊聊补充
              </button>
              <button className="pcm__btn pcm__btn--primary" onClick={onConfirm}>
                确认画像，生成学情概览 →
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
