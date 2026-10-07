import { motion } from 'framer-motion'
import type { PageType } from '../App'
import { useJourney, getJourneyStep, type JourneyStep } from '../store/journey'
import { nextLesson, allLessonsDone } from '../data/learningPath'
import './GuideCard.css'

interface GuideCardProps {
  onNavigate?: (page: PageType) => void
}

interface GuideConfig {
  eyebrow: string
  title: string
  desc: string
  cta: string
  target: PageType
}

/** 首屏状态化引导：根据闭环进度，给出唯一明确的"下一步" */
export default function GuideCard({ onNavigate }: GuideCardProps) {
  const hasDiagnosed = useJourney((s) => s.hasDiagnosed)
  const hasGeneratedPath = useJourney((s) => s.hasGeneratedPath)
  const step = getJourneyStep({ hasDiagnosed, hasGeneratedPath }, allLessonsDone())

  const next = nextLesson()
  const config: Record<JourneyStep, GuideConfig> = {
    diagnose: {
      eyebrow: '第一步 · 画像诊断',
      title: '先做一次能力诊断，了解你的起点',
      desc: '完成一次学情诊断，AI 将生成你的知识画像，作为后续个性化学习的依据。',
      cta: '开始诊断 →',
      target: 'profile',
    },
    'generate-path': {
      eyebrow: '第二步 · 学习路径',
      title: '诊断已完成，生成你的个性化学习路径',
      desc: '基于你的学情画像，AI 将为你规划一条由浅入深的通关路线。',
      cta: '生成学习路径 →',
      target: 'learning-path',
    },
    learn: {
      eyebrow: '第三步 · 学习资源',
      title: `继续学习：${next?.topic ?? ''}`,
      desc: '进入当前章节，获取 AI 定制讲义、实操指南与分阶测试题。',
      cta: '继续学习 →',
      target: 'learning-resource',
    },
    review: {
      eyebrow: '闭环 · 复测更新画像',
      title: '本轮路径已学完，去复测更新画像',
      desc: '通过评估检验掌握度，结果将回流更新你的学情画像，开启下一轮提升。',
      cta: '开始复测 →',
      target: 'profile',
    },
  }
  const g = config[step]

  /* 三步进度指示：done(已完成) / active(当前) / todo(未到) */
  const steps = [
    { key: 'profile', label: '画像诊断', done: hasDiagnosed, active: step === 'diagnose' },
    { key: 'path', label: '学习路径', done: hasGeneratedPath, active: step === 'generate-path' },
    { key: 'learn', label: '学习资源', done: false, active: step === 'learn' || step === 'review' },
  ]

  return (
    <motion.section
      className={`guide-card guide-card--${step}`}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
      aria-label="下一步引导"
    >
      <div className="guide-card__main">
        <span className="guide-card__eyebrow">{g.eyebrow}</span>
        <h2 className="guide-card__title">{g.title}</h2>
        <p className="guide-card__desc">{g.desc}</p>
        <button className="guide-card__cta" type="button" onClick={() => onNavigate?.(g.target)}>
          {g.cta}
        </button>
      </div>

      <ol className="guide-steps" aria-hidden="true">
        {steps.map((s, i) => (
          <li
            key={s.key}
            className={`guide-steps__item ${s.done ? 'is-done' : ''} ${s.active ? 'is-active' : ''}`}
          >
            <span className="guide-steps__badge">{s.done ? '✓' : `${i + 1}`}</span>
            <span className="guide-steps__label">{s.label}</span>
          </li>
        ))}
      </ol>
    </motion.section>
  )
}
