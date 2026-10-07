import { motion } from 'framer-motion'
import './DiagnosisEntry.css'

/**
 * 诊断起点三选一（任务 2）：三种入口产出**同一套画像结构**（能力 ability + 偏好 preference +
 * 主观），仅「来源与置信度」不同——
 *   ① 做题式诊断（对话 + 微测 + 偏好）→ 实测 · 高置信；
 *   ② 一段话描述（复用 /profile/parse 解析）→ 自述 · 中置信；
 *   ③ 直接跳过 → 零基础默认画像（路径从头学）→ 未测 · 默认零基础。
 * 尊重不同用户习惯、如实标注可信度。
 */
interface Props {
  onDialogue: () => void
  onSelfReport: () => void
  onSkip: () => void
  /** 次要入口：传统表单 / 简历上传（保留不删） */
  onForm: () => void
}

interface Card {
  key: 'dialogue' | 'selfreport' | 'skip'
  icon: string
  title: string
  desc: string
  bullets: string[]
  prov: string
  tone: 'high' | 'mid' | 'low'
  cta: string
  video: string
  onClick: (p: Props) => void
}

const CARDS: Card[] = [
  {
    key: 'dialogue',
    icon: '🎯',
    title: '做题式诊断',
    desc: '边聊边测——对话采集 + 诊断微测 + 偏好选择，右侧画像随聊随长。',
    bullets: ['能力靠微测行为反推', '偏好按选择归类型', '最准，约 2-3 分钟'],
    prov: '实测 · 高置信',
    tone: 'high',
    cta: '开始做题诊断 →',
    video: '/diagnosis-video.mp4',
    onClick: (p) => p.onDialogue(),
  },
  {
    key: 'selfreport',
    icon: '✍️',
    title: '一段话描述',
    desc: '用自然语言说说你的水平与目标，AI 自动解析成同一套能力 + 偏好画像。',
    bullets: ['复用画像解析能力', '快，约 30 秒', '自陈推断、非实测'],
    prov: '自述 · 中置信',
    tone: 'mid',
    cta: '写一段话 →',
    video: '/selfreport-video.mp4',
    onClick: (p) => p.onSelfReport(),
  },
  {
    key: 'skip',
    icon: '⏭️',
    title: '直接跳过',
    desc: '不想答题？一键按零基础默认建立画像，学习路径从第一课从头学起。',
    bullets: ['各知识点未掌握', '能力低基线起步', '随时可重测升级'],
    prov: '未测 · 默认零基础',
    tone: 'low',
    cta: '跳过，零基础开始 →',
    video: '/diagnosis-video.mp4',
    onClick: (p) => p.onSkip(),
  },
]

export default function DiagnosisEntry(props: Props) {
  return (
    <div className="de">
      <div className="de__lead">
        <p className="de__lead-text">
          选一种你习惯的方式建立学习画像——<strong>三种都产出同一套画像（能力 + 偏好）</strong>，
          只是来源与可信度不同，我们会如实标注。
        </p>
      </div>

      <div className="de__cards">
        {CARDS.map((c, i) => (
          <motion.button
            key={c.key}
            className={`de-card de-card--${c.tone}`}
            onClick={() => c.onClick(props)}
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.08 }}
            whileHover={{ y: -4 }}
          >
            {/* 左侧视频 */}
            <div className="de-card__video">
              <video autoPlay muted loop playsInline preload="auto">
                <source src={c.video} type="video/mp4" />
              </video>
            </div>
            {/* 右侧文字 */}
            <div className="de-card__text">
              <div className="de-card__title">{c.title}</div>
              <p className="de-card__desc">{c.desc}</p>
              <ul className="de-card__bullets">
                {c.bullets.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
              <span className="de-card__cta">{c.cta}</span>
            </div>
          </motion.button>
        ))}
      </div>

      <div className="de__secondary">
        <span className="de__secondary-hint">更习惯填表，或想上传简历 / 截图补充材料？</span>
        <button className="de__secondary-link" onClick={props.onForm}>
          传统表单 · 简历 / 图片上传 →
        </button>
      </div>
    </div>
  )
}
