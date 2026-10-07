import { useState, useEffect, useRef, useMemo, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import RadarChart from '../components/charts/RadarChart'
import LearningPathCard from '../components/LearningPathCard'
import CountUp from '../components/CountUp'
import GuideCard from '../components/GuideCard'
import LearningEvalPanel from '../components/LearningEvalPanel'
import ProvenanceBadge from '../components/ProvenanceBadge'
import '../components/ProfileDialogue.css'
import { useScrollReveal } from '../hooks/useScrollReveal'
import { useMastery } from '../store/mastery'
import { useJourney } from '../store/journey'
import { usePortrait } from '../store/portrait'
import { KNOWLEDGE_POINTS } from '../data/knowledgePoints'
import { USE_REAL_API } from '../services/api'
import { getDashboardOverview, synthesizeOverview, type DashboardOverview } from '../services/dashboard'
import { getLearningEvaluation, synthesizeEvaluation, type LearningEvaluation } from '../services/learningEval'
import { getResourceHistory } from '../services/resourceHistory'
import { getLearningPath, type LearningPathData } from '../services/learning'
import { listDocuments } from '../services/documentLearning'
import { setResourceNav } from '../services/resourceNav'
import { swrFetch } from '../services/sessionCache'
import { synthesizeSteward } from '../services/stewardActivity'
import { StewardHero, AgentActivityPanel, StewardSuggestPanel } from '../components/LearningSteward'
import { getUser } from '../services/api'
import type { PageType } from '../App'
import './Dashboard.css'

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.08
    }
  }
}

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0 }
}

/** 分钟 → 人性化时长（与学习路径页同口径）。 */
function fmtDuration(min?: number): string {
  if (!min || min <= 0) return ''
  if (min < 60) return `约 ${min} 分钟`
  const h = min / 60
  return `约 ${Number.isInteger(h) ? h : h.toFixed(1)} 小时`
}
/** 分钟 → 小时数（去除 .0）。 */
function toHours(min: number): string {
  const h = Math.round((min / 60) * 10) / 10
  return `${Number.isInteger(h) ? h : h.toFixed(1)}`
}

/* ---------------- 闭环叙事节段（理解你 → 为你规划 → 看你进展 → 给你建议） ---------------- */

interface HubSectionProps {
  step: string
  title: string
  /** 数据来源说明（叙事副标题） */
  source: string
  linkLabel?: string
  onLink?: () => void
  children: ReactNode
}

function HubSection({ step, title, source, linkLabel, onLink, children }: HubSectionProps) {
  return (
    <motion.section className="hub-sec" variants={itemVariants} data-reveal>
      <div className="hub-sec__head">
        <span className="hub-sec__step" aria-hidden="true">
          {step}
        </span>
        <div className="hub-sec__titles">
          <h2 className="hub-sec__title">{title}</h2>
          <span className="hub-sec__source">{source}</span>
        </div>
        {onLink && linkLabel && (
          <button type="button" className="hub-sec__link" onClick={onLink}>
            {linkLabel} →
          </button>
        )}
      </div>
      <div className="hub-sec__body">{children}</div>
    </motion.section>
  )
}

/* ---------------- 全功能入口（功能地图，按学习流程组织） ---------------- */

interface HubEntry {
  key: string
  seq?: number
  name: string
  desc: string
  status?: string
  /** 状态色调：done=绿 / info=默认 / dim=弱 */
  tone?: 'done' | 'info' | 'dim'
  beta?: boolean
  onOpen: () => void
}

function HubEntryGroup({ title, note, entries }: { title: string; note: string; entries: HubEntry[] }) {
  return (
    <div className="hub-map__group">
      <div className="hub-map__group-head">
        <span className="hub-map__group-title">{title}</span>
        <span className="hub-map__group-note">{note}</span>
      </div>
      <div className="hub-map__list">
        {entries.map((e) => (
          <button key={e.key} type="button" className="hub-entry" onClick={e.onOpen}>
            {e.seq && <span className="hub-entry__seq">{e.seq}</span>}
            <span className="hub-entry__body">
              <span className="hub-entry__name">
                {e.name}
                {e.beta && <span className="hub-entry__beta">Beta</span>}
              </span>
              <span className="hub-entry__desc">{e.desc}</span>
            </span>
            {e.status && <span className={`hub-entry__status hub-entry__status--${e.tone ?? 'info'}`}>{e.status}</span>}
            <span className="hub-entry__arrow" aria-hidden="true">
              →
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Dashboard({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  const rootRef = useRef<HTMLDivElement>(null)
  useScrollReveal(rootRef)

  /* 实时时钟（管家「监测中 · HH:mm」）*/
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])
  const hours = time.getHours().toString().padStart(2, '0')
  const minutes = time.getMinutes().toString().padStart(2, '0')

  /* 学情概览随掌握度同步：每通过一个核心知识点，图谱覆盖率 / 已学资源真实增长 */
  const masteryStatus = useMastery((s) => s.status)
  const masteredCore = KNOWLEDGE_POINTS.filter((k) => masteryStatus[k.id] === 'passed').length

  /* 门控：学情概览数据须来自「真实诊断结果 / 画像」，无则视为未诊断（问题 4）。
     Mock 模式以本地画像快照为准（确认画像后写入）；联调以后端 journey.hasDiagnosed 为准。 */
  const journeyHasDiagnosed = useJourney((s) => s.hasDiagnosed)
  const hasGeneratedPath = useJourney((s) => s.hasGeneratedPath)
  const portraitDims = usePortrait((s) => s.dims)
  const portraitTs = usePortrait((s) => s.updatedAt)
  const diagnosed = journeyHasDiagnosed && (USE_REAL_API || portraitDims.length > 0)

  /* 会话缓存 key（带用户维度，登录切换旧缓存自然不命中）。中枢五路数据全部走
     swrFetch（SWR）：回到首页时先秒回上次数据、后台静默重拉再刷新——消除整页干等；
     同 key 并发（StrictMode 双挂载等）共享在途请求，不重复打后端。 */
  const uid = getUser()?.userId ?? 'anon'

  /* 联调数据源：GET /dashboard/overview（接口 29）聚合综合分/强弱项/雷达/对标摘要；
     未诊断不请求。掌握度变化时重取，保持"随掌握度联动"行为 */
  const [overview, setOverview] = useState<DashboardOverview | null>(null)
  useEffect(() => {
    if (!USE_REAL_API || !diagnosed) return
    let alive = true
    swrFetch(`${uid}:hub-overview`, getDashboardOverview, (d) => {
      if (alive) setOverview(d)
    }).catch((e) => console.error('[dashboard] 加载学情概览失败', e)) // 失败回退本地画像合成
    return () => {
      alive = false
    }
  }, [uid, masteryStatus, diagnosed])

  /* 学习过程评估（接口文档 12.2，评估 Agent）：联调取后端真实行为评估；mock 由掌握度合成。
     掌握度变化（做题通过/进度推进）→ 重取，保持评估随学习活动联动。 */
  const [evaluation, setEvaluation] = useState<LearningEvaluation | null>(null)
  useEffect(() => {
    if (!USE_REAL_API || !diagnosed) return
    let alive = true
    swrFetch(`${uid}:hub-evaluation`, getLearningEvaluation, (d) => {
      if (alive) setEvaluation(d)
    }).catch((e) => console.error('[dashboard] 加载学习评估失败', e))
    return () => {
      alive = false
    }
  }, [uid, masteryStatus, diagnosed])
  /* 学情管家·生成 Agent 活动计量：复用我的资源库生成历史（接口 19.1）total 字段。
     mock 返回本地合成条数；联调返回真实累计。仅诊断后拉取，随掌握度变化重取。 */
  const [resourceCount, setResourceCount] = useState(0)
  useEffect(() => {
    if (!diagnosed) return
    let alive = true
    swrFetch(
      `${uid}:hub-resource-count`,
      () => getResourceHistory({ page: 1, pageSize: 1 }),
      (d) => {
        if (alive) setResourceCount(d.total)
      }
    ).catch((e) => console.warn('[steward] 生成历史计量获取失败（已忽略）', e))
    return () => {
      alive = false
    }
  }, [uid, masteryStatus, diagnosed])

  /* 中枢·② 为你规划的路径：复用规划 Agent 的真实路径 + 时间线（6.1 /learning-path）。
     画像变 / 掌握度变 → 重取，与学习路径页同口径；未生成路径不请求、不臆造。 */
  const [path, setPath] = useState<LearningPathData | null>(null)
  useEffect(() => {
    if (!diagnosed || !hasGeneratedPath) return
    let alive = true
    swrFetch(`${uid}:hub-path`, getLearningPath, (d) => {
      if (alive) setPath(d)
    }).catch((e) => console.error('[hub] 拉取学习路径失败', e))
    return () => {
      alive = false
    }
  }, [uid, diagnosed, hasGeneratedPath, masteryStatus, portraitTs])

  /* 中枢·文档学习 Agent 计量：复用我的文档列表（20.2），mock 为本地内存表（无文档=0，不臆造）。 */
  const [docCount, setDocCount] = useState(0)
  useEffect(() => {
    let alive = true
    swrFetch(`${uid}:hub-docs`, listDocuments, (d) => {
      if (alive) setDocCount(d.length)
    }).catch((e) => console.warn('[steward] 文档列表获取失败（已忽略）', e))
    return () => {
      alive = false
    }
  }, [uid])

  const masteredIds = useMemo(
    () => KNOWLEDGE_POINTS.filter((k) => masteryStatus[k.id] === 'passed').map((k) => k.id),
    [masteryStatus]
  )
  const evalData =
    USE_REAL_API && evaluation
      ? evaluation
      : synthesizeEvaluation(
          KNOWLEDGE_POINTS.map((k) => ({ id: k.id, name: k.name })),
          masteredIds
        )

  /* 由真实画像合成的概览（综合分/水平/雷达/强弱项）——确认弹窗同源，口径一致 */
  const synth = useMemo(() => synthesizeOverview(portraitDims), [portraitDims])
  const profileData = USE_REAL_API && overview ? overview : synth
  const radarData = USE_REAL_API && overview ? overview.radar : synth.radar

  /* 图谱覆盖率 / 已学资源属「学习活动」指标：随真实 Mastery 增长，刚诊断未做题=0/空，绝不由画像臆造 */
  const knowledgeGraphCoverage =
    USE_REAL_API && overview ? overview.knowledge_graph_coverage : masteredCore / KNOWLEDGE_POINTS.length
  const learnedResources = USE_REAL_API && overview ? overview.learned_resources : masteredCore
  /* 同级对比同属队列统计：仅联调由后端给出，Mock 下无真实样本则不展示该行 */
  const betterThanPct = USE_REAL_API && overview ? overview.comparison.betterThanPct : null
  const coveragePct = Math.round(knowledgeGraphCoverage * 100)

  /* 岗位对标已降权为「可选轻量模块」：不再占学情概览主信息位，仅在概览末尾折叠回显 + 入口，
     完整对标在「画像诊断」页。数据源照旧（journey / overview.targetSummary），不改口径。 */
  const journeyTargetJobName = useJourney((s) => s.targetJobName)
  const journeyMatchPct = useJourney((s) => s.matchPct)
  const targetHasDiagnosed = USE_REAL_API && overview ? overview.targetSummary.hasDiagnosed : journeyHasDiagnosed
  const targetJobName = USE_REAL_API && overview ? overview.targetSummary.targetJobName : journeyTargetJobName
  const matchPct = USE_REAL_API && overview ? overview.targetSummary.matchPct : journeyMatchPct
  const [jobAsideOpen, setJobAsideOpen] = useState(false)

  /* 学情管家·各 Agent 活动汇总 + 主动建议：纯前端派生自既有能力（journey/portrait/mastery/
     评估 Agent/生成历史/文档列表），不新增后端接口、不改既有口径。 */
  const stewardSummary = useMemo(
    () =>
      synthesizeSteward({
        hasDiagnosed: journeyHasDiagnosed,
        hasGeneratedPath,
        portraitDimCount: portraitDims.length,
        targetJobName: journeyTargetJobName,
        masteredCore,
        totalCore: KNOWLEDGE_POINTS.length,
        resourceCount,
        evaluation: evalData,
        weakTopics: profileData.weak_topics,
        docCount,
      }),
    [
      journeyHasDiagnosed,
      hasGeneratedPath,
      portraitDims.length,
      journeyTargetJobName,
      masteredCore,
      resourceCount,
      evalData,
      profileData.weak_topics,
      docCount,
    ]
  )
  const userName = getUser()?.displayName ?? '学习者_001'

  /* ---- ① 需求推测叙事素材（全部来自真实画像/诊断，无则不说） ---- */
  const learningGoal = portraitDims.find((d) => d.key === 'learning_goal')?.value
  const weakNames = profileData.weak_topics.slice(0, 2).map((t) => t.name)

  /* ---- ② 路径 + 时间线派生（与学习路径页同口径） ---- */
  const lessons = path?.lessons ?? []
  const timeline = path?.summary.timeline
  const narrative = path?.summary.narrative ?? ''
  const currentLesson = lessons.find((l) => l.status !== 'completed') ?? lessons[lessons.length - 1]
  const previewLessons = useMemo(() => {
    if (!lessons.length) return []
    const idx = lessons.findIndex((l) => l.status !== 'completed')
    const start = Math.max(0, Math.min(idx === -1 ? lessons.length - 3 : idx - 1, lessons.length - 3))
    return lessons.slice(start, start + 3)
  }, [lessons])

  /* 「继续学习」：与学习路径页同一跳转协议——当前节点 kpId + flow 有序学习模式 */
  const continueLearning = () => {
    setResourceNav(currentLesson?.kpId ?? '', 'flow')
    onNavigate?.('learning-resource')
  }
  /* 「即时辅导」入口：资源页导学对话 Tab（AskTutorDock/选中即问挂在资源与文档页） */
  const openTutor = () => {
    setResourceNav(currentLesson?.kpId ?? '', 'browse', 'tutor')
    onNavigate?.('learning-resource')
  }

  /* ---- 全功能入口（功能地图）：按学习流程组织，状态全部来自真实数据 ---- */
  const mainlineEntries: HubEntry[] = [
    {
      key: 'profile',
      seq: 1,
      name: '画像诊断',
      desc: '摸清你的知识基础、偏好与目标，生成学习画像',
      status: journeyHasDiagnosed ? '已完成' : '待开始',
      tone: journeyHasDiagnosed ? 'done' : 'dim',
      onOpen: () => onNavigate?.('profile'),
    },
    {
      key: 'learning-path',
      seq: 2,
      name: '学习路径',
      desc: '按画像定制的个性化路线，含预计周期与进度',
      status: hasGeneratedPath ? (timeline ? `进度 ${timeline.completionPct}%` : '已规划') : '待规划',
      tone: hasGeneratedPath ? 'done' : 'dim',
      onOpen: () => onNavigate?.('learning-path'),
    },
    {
      key: 'learning-resource',
      seq: 3,
      name: '学习资源',
      desc: 'AI 生成讲义 / 视频 / 导图 / 图解 / 测验，边学边测',
      status: hasGeneratedPath ? '可进入' : '需先规划',
      tone: hasGeneratedPath ? 'info' : 'dim',
      onOpen: () => onNavigate?.('learning-resource'),
    },
  ]
  const selfEntries: HubEntry[] = [
    {
      key: 'document-learning',
      name: '文档学习',
      desc: '上传你的资料，基于文档生成专属学习资源与问答',
      status: docCount > 0 ? `${docCount} 篇文档` : undefined,
      tone: 'info',
      onOpen: () => onNavigate?.('document-learning'),
    },
    {
      key: 'tutor',
      name: '即时辅导',
      desc: '就当前知识点随时提问，学习内容里选中文字即可问',
      onOpen: openTutor,
    },
  ]
  const assetEntries: HubEntry[] = [
    {
      key: 'my-resources',
      name: '我的资源库',
      desc: '你生成过的全部学习资产，可查看与下载',
      status: resourceCount > 0 ? `${resourceCount} 份` : undefined,
      tone: 'info',
      onOpen: () => onNavigate?.('my-resources'),
    },
    {
      key: 'knowledge-graph',
      name: '知识图谱',
      desc: '7 板块 78 点知识全景，看清你在体系中的位置',
      status: diagnosed ? `覆盖 ${coveragePct}%` : undefined,
      tone: 'info',
      beta: true,
      onOpen: () => onNavigate?.('knowledge-graph'),
    },
    {
      key: 'workflow',
      name: 'Agent工作流',
      desc: '多智能体协同调度过程的可视化大屏',
      onOpen: () => onNavigate?.('workflow'),
    },
  ]

  const hubMap = (
    <motion.section className="hub-map" variants={itemVariants} data-reveal>
      <div className="hub-map__head">
        <h2 className="hub-map__title">功能全景</h2>
        <span className="hub-map__sub">整个平台由管家统筹协同 · 从这里进入任意功能</span>
      </div>
      <div className="hub-map__grid">
        <HubEntryGroup title="核心学习主线" note="按 ①→②→③ 依次推进" entries={mainlineEntries} />
        <HubEntryGroup title="自主学习" note="用自己的材料学 · 随时提问" entries={selfEntries} />
        <HubEntryGroup title="资产与全景" note="学习产出与全局视图" entries={assetEntries} />
      </div>
    </motion.section>
  )

  /* 未诊断：学情概览不展示任何数据，仅留引导态（问题 4）——避免新用户首登即见假数据；
     功能全景仍展示（让新用户一眼看懂整个软件有什么、从哪进）。 */
  if (!diagnosed) {
    return (
      <motion.div
        className="dashboard dashboard--empty"
        ref={rootRef}
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        <motion.div variants={itemVariants}>
          <GuideCard onNavigate={onNavigate} />
        </motion.div>

        <motion.div className="dashboard__empty" variants={itemVariants}>
          <div className="dashboard__empty-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v4" />
              <path d="M12 16h.01" />
            </svg>
          </div>
          <h2 className="dashboard__empty-title">学情管家待启动</h2>
          <p className="dashboard__empty-desc">
            AI 学情管家会持续监测你的学习全过程，协调诊断/规划/生成/评估/文档学习各专家 Agent 为你服务。
            综合评分、知识雷达、优势与盲区都来自你的<strong>真实学习画像</strong>——
            先完成一次画像诊断，管家才能据此开始监测并给出建议。
          </p>
          <button className="dashboard__empty-cta" type="button" onClick={() => onNavigate?.('profile')}>
            先完成画像诊断 →
          </button>
        </motion.div>

        {hubMap}
      </motion.div>
    )
  }

  return (
    <motion.div
      className="dashboard"
      ref={rootRef}
      variants={containerVariants}
      initial="hidden"
      animate="visible"
    >
      {/* 学情管家身份 + 全局学情监测：统领整页的「管家」角色叙事 */}
      <motion.div variants={itemVariants}>
        <StewardHero
          userName={userName}
          overallLevel={profileData.overall_level}
          overallScore={profileData.overall_score}
          masteredCore={masteredCore}
          totalCore={KNOWLEDGE_POINTS.length}
          coveragePct={coveragePct}
          resourceCount={resourceCount}
          activeAgents={stewardSummary.activeAgents}
          totalAgents={stewardSummary.totalAgents}
          monitoredAt={`${hours}:${minutes}`}
        />
      </motion.div>

      {/* 首屏状态化引导：唯一明确的下一步 */}
      <motion.div variants={itemVariants}>
        <GuideCard onNavigate={onNavigate} />
      </motion.div>

      {/* ==================== 管家闭环：理解你 → 为你规划 → 看你进展 → 给你建议 ==================== */}
      <div className="hub-loop">
        {/* ① 我对你的理解（需求推测 · 来自画像诊断） */}
        <HubSection
          step="01"
          title="我对你的理解"
          source="需求推测 · 来自画像诊断"
          linkLabel="查看完整画像"
          onLink={() => onNavigate?.('profile')}
        >
          <div className="hub-infer">
            <p className="hub-infer__text">
              {learningGoal || targetJobName ? (
                <>
                  我推测你想<strong>{learningGoal ?? '系统提升领域能力'}</strong>
                  {targetJobName && (
                    <>
                      ，目标岗位「<strong>{targetJobName}</strong>」
                    </>
                  )}
                  ；
                </>
              ) : null}
              当前处于 <strong>{profileData.overall_level}</strong> 水平（综合 {profileData.overall_score} 分），
              {weakNames.length > 0 ? (
                <>
                  在 <strong>{weakNames.join('、')}</strong> 相对薄弱。
                </>
              ) : (
                <>暂未发现明显薄弱项。</>
              )}
            </p>
            <div className="hub-infer__meta">
              {portraitDims.length > 0 && <ProvenanceBadge dims={portraitDims} showHint />}
              {betterThanPct !== null && (
                <span className="hub-infer__compare">超过了 {betterThanPct}% 的同级学习者</span>
              )}
            </div>
          </div>

          <div className="dashboard__grid">
            {/* Radar Chart */}
            <div className="dashboard__card dashboard__card--chart">
              <div className="card-header">
                <h3>能力掌握雷达图</h3>
                <span className="card-badge">微测实测</span>
              </div>
              <div className="card-content">
                <RadarChart data={radarData} />
              </div>
            </div>

            {/* 能力分析 */}
            <div className="dashboard__card dashboard__card--ability">
              <div className="card-header">
                <h3>能力分析</h3>
              </div>
              <div className="card-content">
                <div className="ability-analysis">
                  <div className="ability-analysis__tags">
                    <div className="ability-analysis__section">
                      <span className="ability-analysis__label ability-analysis__label--success">
                        <span className="ability-analysis__dot ability-analysis__dot--success" />
                        优势领域
                      </span>
                      <div className="ability-analysis__tag-group">
                        {profileData.strong_topics.map((topic, i) => (
                          <div key={i} className="ability-item">
                            <div className="ability-item__head">
                              <span className="ability-item__name">{topic.name}</span>
                              <span className="ability-item__pct">{topic.mastery}%</span>
                            </div>
                            <div className="ability-meter">
                              <span className="ability-meter__fill ability-meter__fill--strong" style={{ width: `${topic.mastery}%` }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="ability-analysis__section">
                      <span className="ability-analysis__label ability-analysis__label--warning">
                        <span className="ability-analysis__dot ability-analysis__dot--warning" />
                        待提升领域
                      </span>
                      <div className="ability-analysis__tag-group">
                        {profileData.weak_topics.map((topic, i) => (
                          <div key={i} className="ability-item">
                            <div className="ability-item__head">
                              <span className="ability-item__name">{topic.name}</span>
                              <span className="ability-item__pct">{topic.mastery}%</span>
                            </div>
                            <div className="ability-meter">
                              <span className="ability-meter__fill ability-meter__fill--weak" style={{ width: `${topic.mastery}%` }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                    {/* C2 偏好画像：类型标签（无分数、不上雷达轴） */}
                    {profileData.preferences?.length > 0 && (
                      <div className="ability-analysis__section">
                        <span className="ability-analysis__label ability-analysis__label--pref">
                          <span className="ability-analysis__dot ability-analysis__dot--pref" />
                          学习偏好 · 归类型不打分
                        </span>
                        <div className="ability-pref-tags">
                          {profileData.preferences.map((p) => (
                            <span key={p.key} className="ability-pref-tag">
                              <span className="ability-pref-tag__type">{p.value}</span>
                              <span className="ability-pref-tag__dim">{p.label}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* 行动建议：撑满卡片下方，给出下一步 + 主色 CTA */}
                  <div className="ability-action">
                    <p className="ability-action__tip">
                      <span className="ability-action__tip-icon">✦</span>
                      {profileData.weak_topics.length ? (
                        <>建议优先攻克 <strong>{profileData.weak_topics[0].name}</strong>，预计 3 节课可提升</>
                      ) : (
                        <>各维度较均衡，建议按学习路径稳步推进</>
                      )}
                    </p>
                    <button className="ability-action__cta" type="button" onClick={continueLearning}>
                      生成针对性练习 →
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </HubSection>

        {/* ② 为你规划的路径（规划 Agent · 路径 + 时间线） */}
        <HubSection
          step="02"
          title="为你规划的路径"
          source="规划 Agent · 路径 + 时间线"
          linkLabel="查看完整路径"
          onLink={() => onNavigate?.('learning-path')}
        >
          {hasGeneratedPath && timeline && timeline.totalCount > 0 ? (
            <div className="hub-plan">
              <div className="dashboard__card hub-plan__main">
                <div className="card-header">
                  <h3>路径概要</h3>
                  <span className="card-badge">
                    预计 {timeline.cycleWeeks} 周学完
                  </span>
                </div>
                <div className="card-content">
                  {narrative && <p className="hub-plan__why">✦ {narrative}</p>}
                  <div className="hub-plan__facts">
                    <span className="hub-plan__fact"><b>{timeline.totalCount}</b> 个知识点</span>
                    <span className="hub-plan__fact">共约 <b>{timeline.totalHours}</b> 小时</span>
                    <span className="hub-plan__fact">已学 <b>{timeline.learnedCount}/{timeline.totalCount}</b></span>
                    <span className="hub-plan__fact">已投入约 <b>{toHours(timeline.spentMinutes)}</b> 小时</span>
                  </div>
                  <div className="hub-plan__progress">
                    <div className="hub-plan__bar">
                      <motion.div
                        className="hub-plan__fill"
                        initial={{ width: 0 }}
                        animate={{ width: `${timeline.completionPct}%` }}
                        transition={{ duration: 0.9, ease: 'easeOut' }}
                      />
                    </div>
                    <span className="hub-plan__pct">{timeline.completionPct}%</span>
                  </div>
                  <div className="learning-path-preview">
                    {previewLessons.map((l) => (
                      <LearningPathCard
                        key={l.sequence}
                        sequence={l.sequence}
                        topic={l.topic}
                        difficulty={l.difficulty}
                        status={l.status}
                        progress={l.progress}
                        estimatedMinutes={l.estimatedMinutes}
                        onOpen={() => {
                          // 与「继续学习」同一跳转协议：未完成 → 有序学习流；已完成 → 自由浏览复习
                          setResourceNav(l.kpId ?? '', l.status === 'completed' ? 'browse' : 'flow')
                          onNavigate?.('learning-resource')
                        }}
                      />
                    ))}
                  </div>
                </div>
              </div>

              <div className="dashboard__card hub-plan__next">
                <div className="card-header">
                  <h3>下一步 · 你在这里</h3>
                  <span className="card-badge">⏱ {timeline.pacing}</span>
                </div>
                <div className="card-content">
                  {currentLesson ? (
                    <>
                      <p className="hub-plan__next-topic">{currentLesson.topic}</p>
                      {currentLesson.estimatedMinutes ? (
                        <p className="hub-plan__next-eta">{fmtDuration(currentLesson.estimatedMinutes)}</p>
                      ) : null}
                      {currentLesson.description && (
                        <p className="hub-plan__next-desc">{currentLesson.description}</p>
                      )}
                      <button type="button" className="hub-plan__cta" onClick={continueLearning}>
                        继续学习 →
                      </button>
                    </>
                  ) : (
                    <p className="hub-plan__next-desc">路径已全部学完，去复测更新画像吧。</p>
                  )}
                </div>
              </div>
            </div>
          ) : hasGeneratedPath ? (
            /* 已规划但数据在途（或后端无时间线）：不误报「未规划」，静默同步态 */
            <div className="dashboard__card hub-plan__empty">
              <p className="hub-plan__empty-text">正在同步你的学习路径与时间线…</p>
            </div>
          ) : (
            <div className="dashboard__card hub-plan__empty">
              <p className="hub-plan__empty-text">
                画像已就绪，但还没有生成学习路径——规划 Agent 会按你的画像定制路线与时间安排。
              </p>
              <button type="button" className="hub-plan__cta" onClick={() => onNavigate?.('learning-path')}>
                去生成学习路径 →
              </button>
            </div>
          )}
        </HubSection>

        {/* ③ 学习监测（掌握度 + 各专家 Agent 活动） */}
        <HubSection
          step="03"
          title="学习监测"
          source="掌握度 + 各专家 Agent 活动"
          linkLabel="看协同大屏"
          onLink={() => onNavigate?.('workflow')}
        >
          {/* 掌握度概览：随真实 Mastery 增长 */}
          <div className="dashboard__stats">
            {/* 知识图谱覆盖 */}
            <div className="stat-card">
              <div className="stat-card__top">
                <div className="stat-card__icon stat-card__icon--blue">
                  <svg viewBox="0 0 24 24" fill="none">
                    <defs>
                      <linearGradient id="sc-blue" x1="0" y1="0" x2="24" y2="24">
                        <stop style={{ stopColor: 'var(--primary)' }} /><stop offset="1" style={{ stopColor: 'var(--primary-d)' }} />
                      </linearGradient>
                    </defs>
                    <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="url(#sc-blue)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <svg className="stat-card__sparkline" viewBox="0 0 80 30" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id="spark-blue" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0.22 }} />
                      <stop offset="100%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0 }} />
                    </linearGradient>
                  </defs>
                  <path d="M0 25 Q10 20, 16 18 T32 14 T48 16 T64 8 T80 5" fill="none" style={{ stroke: 'var(--ink-soft)' }} strokeWidth="2" strokeLinecap="round" />
                  <path d="M0 25 Q10 20, 16 18 T32 14 T48 16 T64 8 T80 5 L80 30 L0 30Z" fill="url(#spark-blue)" />
                </svg>
              </div>
              <div className="stat-card__content">
                <CountUp className="stat-card__value" value={Math.round(knowledgeGraphCoverage * 100)} suffix="%" />
                <span className="stat-card__label">知识图谱覆盖</span>
              </div>
            </div>

            {/* 优势知识点 */}
            <div className="stat-card">
              <div className="stat-card__top">
                <div className="stat-card__icon stat-card__icon--green">
                  <svg viewBox="0 0 24 24" fill="none">
                    <defs>
                      <linearGradient id="sc-green" x1="0" y1="0" x2="24" y2="24">
                        <stop stopColor="#34d399" /><stop offset="1" stopColor="#059669" />
                      </linearGradient>
                    </defs>
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" stroke="url(#sc-green)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <svg className="stat-card__sparkline" viewBox="0 0 80 30" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id="spark-green" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0.22 }} />
                      <stop offset="100%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0 }} />
                    </linearGradient>
                  </defs>
                  <path d="M0 22 Q12 18, 20 20 T40 12 T60 15 T80 6" fill="none" style={{ stroke: 'var(--ink-soft)' }} strokeWidth="2" strokeLinecap="round" />
                  <path d="M0 22 Q12 18, 20 20 T40 12 T60 15 T80 6 L80 30 L0 30Z" fill="url(#spark-green)" />
                </svg>
              </div>
              <div className="stat-card__content">
                <CountUp className="stat-card__value" value={profileData.strong_topics.length} />
                <span className="stat-card__label">优势知识点</span>
              </div>
            </div>

            {/* 知识盲区 */}
            <div className="stat-card stat-card--warning">
              <div className="stat-card__top">
                <div className="stat-card__icon stat-card__icon--amber">
                  <svg viewBox="0 0 24 24" fill="none">
                    <defs>
                      <linearGradient id="sc-amber" x1="0" y1="0" x2="24" y2="24">
                        <stop style={{ stopColor: 'var(--accent)' }} /><stop offset="1" style={{ stopColor: 'var(--accent)' }} />
                      </linearGradient>
                    </defs>
                    <circle cx="12" cy="12" r="10" stroke="url(#sc-amber)" strokeWidth="1.8" />
                    <line x1="12" y1="8" x2="12" y2="12" stroke="url(#sc-amber)" strokeWidth="2" strokeLinecap="round" />
                    <circle cx="12" cy="16" r="1" fill="url(#sc-amber)" />
                  </svg>
                </div>
                <svg className="stat-card__sparkline" viewBox="0 0 80 30" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id="spark-amber" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0.22 }} />
                      <stop offset="100%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0 }} />
                    </linearGradient>
                  </defs>
                  <path d="M0 15 Q10 20, 20 14 T40 18 T60 10 T80 12" fill="none" style={{ stroke: 'var(--ink-soft)' }} strokeWidth="2" strokeLinecap="round" />
                  <path d="M0 15 Q10 20, 20 14 T40 18 T60 10 T80 12 L80 30 L0 30Z" fill="url(#spark-amber)" />
                </svg>
              </div>
              <div className="stat-card__content">
                <CountUp className="stat-card__value" value={profileData.weak_topics.length} />
                <span className="stat-card__label">知识盲区</span>
              </div>
            </div>

            {/* 已学资源 */}
            <div className="stat-card">
              <div className="stat-card__top">
                <div className="stat-card__icon stat-card__icon--purple">
                  <svg viewBox="0 0 24 24" fill="none">
                    <defs>
                      <linearGradient id="sc-purple" x1="0" y1="0" x2="24" y2="24">
                        <stop style={{ stopColor: 'var(--primary-d)' }} /><stop offset="1" style={{ stopColor: 'var(--primary-d)' }} />
                      </linearGradient>
                    </defs>
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="url(#sc-purple)" strokeWidth="1.8" />
                    <polyline points="14 2 14 8 20 8" stroke="url(#sc-purple)" strokeWidth="1.8" strokeLinecap="round" />
                  </svg>
                </div>
                <svg className="stat-card__sparkline" viewBox="0 0 80 30" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id="spark-purple" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0.22 }} />
                      <stop offset="100%" style={{ stopColor: 'var(--ink-soft)', stopOpacity: 0 }} />
                    </linearGradient>
                  </defs>
                  <path d="M0 20 Q15 15, 25 18 T50 10 T80 4" fill="none" style={{ stroke: 'var(--ink-soft)' }} strokeWidth="2" strokeLinecap="round" />
                  <path d="M0 20 Q15 15, 25 18 T50 10 T80 4 L80 30 L0 30Z" fill="url(#spark-purple)" />
                </svg>
              </div>
              <div className="stat-card__content">
                <CountUp className="stat-card__value" value={learnedResources} />
                <span className="stat-card__label">已学资源</span>
              </div>
            </div>
          </div>

          {/* 学习评估面板（接口文档 12.2，评估 Agent）：多维评估 + 方法建议 + 动态调整 */}
          <LearningEvalPanel evaluation={evalData} onNavigate={onNavigate} />

          {/* 各专家 Agent 活动看板：诊断 / 规划 / 生成 / 评估 / 文档学习 */}
          <AgentActivityPanel summary={stewardSummary} onNavigate={onNavigate} />
        </HubSection>

        {/* ④ 管家建议（基于监测的主动建议） */}
        <HubSection step="04" title="管家建议" source="基于监测的主动建议">
          <StewardSuggestPanel summary={stewardSummary} onNavigate={onNavigate} />
          <p className="hub-loopback">
            <span className="hub-loopback__icon" aria-hidden="true">↺</span>
            闭环运转中——你执行建议产生的新学情（做题、进度、复测）会回流更新
            「① 我对你的理解」，路径与建议随之持续校准。
          </p>
        </HubSection>
      </div>

      {/* ==================== 全功能入口：功能地图 ==================== */}
      {hubMap}

      {/* 岗位对标（可选轻量模块）：已从学情概览主信息位降权——折叠在概览末尾，默认收起，
          不占主视觉、不参与「真实画像→掌握度→学习评估」主叙事；完整对标仍在「画像诊断」页。 */}
      <motion.div className="dashboard__job-aside" variants={itemVariants}>
        <button
          type="button"
          className="dashboard__job-aside-head"
          aria-expanded={jobAsideOpen}
          onClick={() => setJobAsideOpen((v) => !v)}
        >
          <span className="dashboard__job-aside-icon">🎯</span>
          <span className="dashboard__job-aside-title">岗位对标</span>
          <span className="dashboard__job-aside-tag">可选</span>
          {targetHasDiagnosed && targetJobName && (
            <span className="dashboard__job-aside-summary">
              {targetJobName} · {matchPct ?? 0}%
            </span>
          )}
          <span className={`dashboard__job-aside-chevron${jobAsideOpen ? ' is-open' : ''}`} aria-hidden="true">⌄</span>
        </button>
        {jobAsideOpen && (
          <div className="dashboard__job-aside-body">
            {targetHasDiagnosed && targetJobName ? (
              <p className="dashboard__job-aside-text">
                目标岗位 <strong>{targetJobName}</strong>，当前对标进度 <strong>{matchPct ?? 0}%</strong>。
                岗位对标为可选参考，不影响学情概览与学习路径。
              </p>
            ) : (
              <p className="dashboard__job-aside-text">
                还没有做岗位对标。它是可选项——想了解自己与某个目标岗位的能力差距时再用。
              </p>
            )}
            <button
              type="button"
              className="dashboard__job-aside-cta"
              onClick={() => onNavigate?.('profile')}
            >
              前往岗位对标 →
            </button>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}
