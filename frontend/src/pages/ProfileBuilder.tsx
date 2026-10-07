import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import CountUp from '../components/CountUp'
import PageHeader from '../components/PageHeader'
import RadarChart from '../components/charts/RadarChart'
import JobMarketPanel from '../components/JobMarketPanel'
import { revealItem } from '../components/Reveal'
import ProfileDialogue from '../components/ProfileDialogue'
import DiagnosisEntry from '../components/DiagnosisEntry'
import SelfReportProfile from '../components/SelfReportProfile'
import type { PageType } from '../App'
import { useJourney } from '../store/journey'
import { usePortrait } from '../store/portrait'
import type { JobMarket } from '../services/jobMarket'
import { CANONICAL_DIMS, saveStudentPortrait, zeroBasePortrait, type PortraitDimension } from '../services/profileDialogue'
import { generateNarrative, type Narrative, type SourceRef } from '../services/profileNarrative'
import { USE_REAL_API } from '../services/api'
import { generateNarrativeApi, parseProfile, completeDiagnosisApi } from '../services/profile'
import './ProfileBuilder.css'

/* 雷达 6 维：与岗位画像 radar 字段对齐，作为「当前能力 vs 岗位要求」对标的统一坐标系 */
const RADAR_DIMS = ['机器学习基础', '神经网络', '深度学习', '注意力机制', 'Transformer', '大模型微调'] as const

const educationLevels = ['本科', '硕士', '博士', '其他']
const majorFields = ['计算机科学', '电子信息', '人工智能', '软件工程', '数据科学', '其他']
const learningGoals = ['职业培训', '技能认证', '学术研究', '兴趣学习', '其他']

/* 解析来源：决定 Step2 的来源徽章配色与文案 */
type Source = 'resume' | 'ocr' | 'text' | 'manual'
const SOURCE_META: Record<Source, { label: string; tone: string }> = {
  resume: { label: '简历解析', tone: 'resume' },
  ocr: { label: '图片 OCR', tone: 'ocr' },
  text: { label: '文字描述', tone: 'text' },
  manual: { label: '手动填写', tone: 'manual' },
}

interface Basic {
  value: string
  source: Source
}
interface SkillItem {
  /** 对应 RADAR_DIMS 之一 */
  name: string
  level: number
  source: Source
}
interface ParsedProfile {
  education: Basic
  major: Basic
  goal: Basic
  skills: SkillItem[]
}

/** 空白手动画像：手动填写兜底入口产出，全部来源标 manual，技能默认 50 */
function emptyProfile(): ParsedProfile {
  return {
    education: { value: '', source: 'manual' },
    major: { value: '', source: 'manual' },
    goal: { value: '', source: 'manual' },
    skills: RADAR_DIMS.map((name) => ({ name, level: 50, source: 'manual' })),
  }
}

/** canonical 维度标签表（与对话诊断 17.2 同一套 key/label，保证三条路径同维度） */
const CANON_LABEL: Record<string, string> = Object.fromEntries(CANONICAL_DIMS.map((d) => [d.key, d.label]))

/**
 * 把简历 / 手动表单输入映射为「与对话诊断同一套 canonical 异质画像维度」。
 * 注意：RADAR_DIMS 的 6 维知识点自评是「知识掌握/岗位对标雷达」（另一类图、并存不变），
 * 这里产出的是「学情概览」用的权威异质画像——三条路径据此完全一致、不再另造一套雷达。
 * 表单未直接采集的维度（认知风格 / 学习节奏）标 inferred + 低置信度（防杜撰，留待对话补全）。
 */
function toCanonicalPortrait(draft: ParsedProfile): PortraitDimension[] {
  const skills = draft.skills
  const avg = skills.length ? Math.round(skills.reduce((s, k) => s + k.level, 0) / skills.length) : 50
  const weakest = skills.reduce((min, k) => (k.level < min.level ? k : min), skills[0])
  const dim = (
    key: string,
    value: string,
    confidence: number,
    source: PortraitDimension['source'],
    score?: number
  ): PortraitDimension => ({ key, label: CANON_LABEL[key] ?? key, value, score, confidence, source })

  return [
    dim('knowledge_base', `${draft.major.value || '专业'}背景，能力自评均值 ${avg}`, 0.7, 'manual', avg),
    dim('prior_experience', `${draft.education.value || '—'} · ${draft.major.value || '—'}`, 0.7, 'manual'),
    dim('learning_goal', draft.goal.value || '—', 0.8, 'manual'),
    dim('error_preference', `${weakest?.name ?? '部分知识点'}偏薄弱（自评 ${weakest?.level ?? 0}）`, 0.5, 'inferred'),
    dim('cognitive_style', '待对话诊断补全', 0.4, 'inferred'),
    dim('learning_pace', '待对话诊断补全', 0.4, 'inferred'),
  ]
}

/**
 * 多模态解析（前端模拟）：依据本次提供的输入通道（简历 / 图片 / 文字）产出结构化画像，
 * 每个字段标注其来源。后端就绪后，仅需把这里替换为真实解析 service，Step2/Step3 UI 不变。
 */
function mockParse(channels: { resume: boolean; image: boolean; text: boolean }): ParsedProfile {
  // 来源优先级：简历 > 图片OCR > 文字；至少有一个通道
  const primary: Source = channels.resume ? 'resume' : channels.image ? 'ocr' : 'text'
  const secondary: Source = channels.text ? 'text' : primary
  return {
    education: { value: '硕士', source: primary },
    major: { value: '人工智能', source: primary },
    goal: { value: '职业培训', source: secondary },
    skills: [
      { name: '机器学习基础', level: 85, source: primary },
      { name: '神经网络', level: 72, source: primary },
      { name: '深度学习', level: 68, source: secondary },
      { name: '注意力机制', level: 45, source: channels.image ? 'ocr' : secondary },
      { name: 'Transformer', level: 30, source: secondary },
      { name: '大模型微调', level: 20, source: secondary },
    ],
  }
}

/* Gap 分档：当前/要求 比值 → 三档 */
type Tier = 'met' | 'partial' | 'missing'
function tierOf(current: number, required: number): Tier {
  if (required <= 0 || current >= required) return 'met'
  return current / required >= 0.6 ? 'partial' : 'missing'
}
const TIER_META: Record<Tier, { label: string; cls: string }> = {
  met: { label: '已具备', cls: 'met' },
  partial: { label: '部分掌握', cls: 'partial' },
  missing: { label: '缺失', cls: 'missing' },
}

type Step = 'collect' | 'parsing' | 'confirm' | 'benchmark'
const STEP_INDEX: Record<Step, number> = { collect: 0, parsing: 0, confirm: 1, benchmark: 2 }
const STEP_LABELS = ['信息采集', '确认画像', '岗位对标']

export default function ProfileBuilder({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  const completeDiagnosis = useJourney((s) => s.completeDiagnosis)
  const setPortrait = usePortrait((s) => s.setPortrait)
  /* 诊断起点三选一（任务 2）：choose=入口选择 · dialogue=做题式诊断 · selfreport=一段话自述
     · form=传统表单/材料上传（次要入口，保留不删）。三入口产出同一套画像、仅来源置信度不同。 */
  const [mode, setMode] = useState<'choose' | 'dialogue' | 'selfreport' | 'form'>('choose')
  const [step, setStep] = useState<Step>('collect')

  /* Step1 采集状态（raw 为真实 File，仅联调上传用，不参与 UI 渲染）*/
  const [files, setFiles] = useState<{ name: string; kind: 'doc' | 'image'; raw?: File }[]>([])
  const [description, setDescription] = useState('')
  const [targetJob, setTargetJob] = useState<JobMarket | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  /* 解析产物（Step2 可编辑） */
  const [draft, setDraft] = useState<ParsedProfile | null>(null)
  /* 当前点击回看的来源 id（高亮 ⇄ 来源 chip 联动） */
  const [activeSource, setActiveSource] = useState<string | null>(null)

  const hasDoc = files.some((f) => f.kind === 'doc')
  const hasImage = files.some((f) => f.kind === 'image')
  const hasInput = files.length > 0 || description.trim().length > 0
  const onSelectJob = useCallback((j: JobMarket | null) => setTargetJob(j), [])
  /* 岗位数据离线状态（由 JobMarketPanel 上抛）：页级「联网快照」标签跟随显示 */
  const [jobOffline, setJobOffline] = useState(false)
  const onJobOffline = useCallback((o: boolean) => setJobOffline(o), [])

  const addFiles = (list: FileList | null) => {
    if (!list) return
    const next = Array.from(list).map((f) => ({
      name: f.name,
      kind: /\.(png|jpe?g|webp|gif|bmp)$/i.test(f.name) ? ('image' as const) : ('doc' as const),
      raw: f,
    }))
    setFiles((prev) => [...prev, ...next])
  }

  /* 智能解析：进入解析动画 → 产出结构化画像 → 确认页 */
  const runParse = () => {
    setStep('parsing')
    if (USE_REAL_API) {
      // 联调：上传真实材料给 POST /profile/parse，映射为可编辑 draft
      const raws = files.map((f) => f.raw).filter((f): f is File => !!f)
      parseProfile(raws, description)
        .then((p) => {
          setDraft({
            education: { value: p.education.value, source: p.education.source as Source },
            major: { value: p.major.value, source: p.major.source as Source },
            goal: { value: p.goal.value, source: p.goal.source as Source },
            skills: p.skills.map((s) => ({ name: s.name, level: s.level, source: s.source as Source })),
          })
          setStep('confirm')
        })
        .catch((e) => {
          // 解析失败兜底为 mock，保证演示不中断（错误已记录）
          console.error('[profile] parse 失败，回退 mock', e)
          setDraft(mockParse({ resume: hasDoc, image: hasImage, text: description.trim().length > 0 }))
          setStep('confirm')
        })
      return
    }
    setTimeout(() => {
      setDraft(mockParse({ resume: hasDoc, image: hasImage, text: description.trim().length > 0 }))
      setStep('confirm')
    }, 2600)
  }

  /* 手动填写兜底：跳过解析，直接进入确认页编辑空白画像 */
  const startManual = () => {
    setDraft(emptyProfile())
    setStep('confirm')
  }

  const updateBasic = (key: 'education' | 'major' | 'goal', value: string) => {
    setDraft((d) => (d ? { ...d, [key]: { value, source: 'manual' as Source } } : d))
  }
  const updateSkill = (name: string, level: number) => {
    setDraft((d) =>
      d
        ? { ...d, skills: d.skills.map((s) => (s.name === name ? { ...s, level, source: 'manual' as Source } : s)) }
        : d
    )
  }

  /* Step3 对标计算：当前能力（来自 draft）对齐 RADAR_DIMS，对标线取目标岗位 radar */
  const currentValues = useMemo(
    () => RADAR_DIMS.map((dim) => draft?.skills.find((s) => s.name === dim)?.level ?? 0),
    [draft]
  )
  const radarTarget = useMemo(
    () => (targetJob ? RADAR_DIMS.map((dim) => targetJob.radar[dim] ?? 0) : undefined),
    [targetJob]
  )
  const gaps = useMemo(
    () =>
      RADAR_DIMS.map((dim, i) => {
        const required = targetJob?.radar[dim] ?? 0
        const current = currentValues[i]
        return { dim, current, required, gap: required - current, tier: tierOf(current, required) }
      }),
    [currentValues, targetJob]
  )
  const metCount = gaps.filter((g) => g.tier === 'met').length
  const matchPct = targetJob ? Math.round((metCount / RADAR_DIMS.length) * 100) : 0

  /* AI 画像综述：基于结构化字段 + 原始材料引用生成（grounding）；无材料则为 null */
  const materials = useMemo<SourceRef[]>(() => {
    const m: SourceRef[] = files.map((f, i) => ({
      id: `file-${i}`,
      label: f.name,
      kind: f.kind === 'image' ? ('image' as const) : ('doc' as const),
    }))
    if (description.trim()) m.push({ id: 'desc', label: '文字描述', kind: 'text' })
    return m
  }, [files, description])
  /* mock 叙述：前端模板合成（grounding）；联调时由后端 /profile/narrative 覆盖 */
  const localNarrative = useMemo(
    () => (draft ? generateNarrative(draft, materials, targetJob) : null),
    [draft, materials, targetJob]
  )
  const [apiNarrative, setApiNarrative] = useState<Narrative | null>(null)
  useEffect(() => {
    if (!USE_REAL_API) return
    if (!draft || materials.length === 0) {
      setApiNarrative(null)
      return
    }
    let cancelled = false
    generateNarrativeApi(
      { education: draft.education, major: draft.major, goal: draft.goal, skills: draft.skills },
      materials,
      targetJob ? { name: targetJob.name, radar: targetJob.radar } : null
    )
      .then((n) => {
        if (!cancelled) setApiNarrative(n)
      })
      .catch((e) => {
        console.error('[profile] narrative 生成失败', e)
        if (!cancelled) setApiNarrative(null)
      })
    return () => {
      cancelled = true
    }
  }, [draft, materials, targetJob])
  const narrative = USE_REAL_API ? apiNarrative : localNarrative
  /* 回看来源标签：优先取叙述自带 sources（联调时来源 id 由后端给出），回退本地 materials */
  const activeSourceLabel =
    narrative?.sources.find((s) => s.id === activeSource)?.label ??
    materials.find((m) => m.id === activeSource)?.label

  /* 画像覆盖写：写入 store（学情概览据此合成）；联调时覆盖写后端 StudentPortrait。
     三入口共用——保证产出同一份、同维度的权威画像，重做即覆盖、不分叉。 */
  const writePortrait = (snapshot: PortraitDimension[]) => {
    setPortrait(snapshot)
    if (USE_REAL_API) {
      saveStudentPortrait(snapshot).catch((e) => console.error('[profile] 画像覆盖写入失败', e))
    }
  }

  const finish = async (jobInfo?: { targetJobName: string; matchPct: number }) => {
    // 表单入口：把多模态输入映射为「与对话诊断同一套 canonical 维度」的权威画像（不再另造一套雷达）。
    // 对话入口画像已由 ProfileDialogue 自行落库，此处 draft 为空、不重复写。
    if (draft) writePortrait(toCanonicalPortrait(draft))
    // 对话路径：用对话算出的岗位匹配（jobInfo）；表单路径：用 Step3 岗位对标结果
    const info = jobInfo ?? (targetJob ? { targetJobName: targetJob.name, matchPct } : undefined)
    completeDiagnosis(info)
    // 等待后端写入 has_diagnosed 后再跳转，避免 learning-path 竞态拿到种子路径
    if (USE_REAL_API && info) {
      try { await completeDiagnosisApi(info.targetJobName, info.matchPct) } catch { /* 已乐观更新 */ }
    }
    onNavigate?.('learning-path')
  }

  /* 入口②/③ 直接拿到「同一套画像结构」的成品维度 → 覆盖写并完成诊断（不带岗位匹配）。
     零基础画像不写任何 Mastery passed → 学习路径全节点未开始、绝不出现「已完成」。 */
  const finishWithPortrait = async (dims: PortraitDimension[]) => {
    writePortrait(dims)
    completeDiagnosis({ targetJobName: '', matchPct: 0 })
    if (USE_REAL_API) {
      try { await completeDiagnosisApi('', 0) } catch { /* 已乐观更新 */ }
    }
    onNavigate?.('learning-path')
  }

  const SUBTITLE: Record<typeof mode, string> = {
    choose: '三种入口任你选——做题式 / 一段话描述 / 直接跳过，产出同一套画像，如实标注来源与可信度',
    dialogue: '摒弃繁琐表单——用自然语言对话自动抽取特征，右侧动态画像随聊随长',
    selfreport: '用一段话描述你的水平与目标，AI 自动解析成「能力 + 偏好」画像（自述 · 中置信）',
    form: '多模态采集你的能力信息，结构化确认后与目标岗位实时对标，生成针对性学习路径',
  }

  return (
    <div className={`profile-builder ${mode !== 'form' ? 'profile-builder--wide' : ''}`}>
      <PageHeader
        title="学习者画像诊断"
        highlight="画像"
        subtitle={SUBTITLE[mode]}
        crumb="画像诊断"
        onBack={() => onNavigate?.('dashboard')}
      />

      {/* ============ 诊断起点：三选一 ============ */}
      {mode === 'choose' && (
        <DiagnosisEntry
          onDialogue={() => setMode('dialogue')}
          onSelfReport={() => setMode('selfreport')}
          onSkip={() => finishWithPortrait(zeroBasePortrait())}
          onForm={() => setMode('form')}
        />
      )}

      {/* ============ 入口①：做题式（对话 + 微测 + 偏好），实测·高置信 ============ */}
      {mode === 'dialogue' && (
        <>
          <ProfileDialogue onFinish={finish} />
          <div className="pb-secondary">
            <button className="pb-secondary__link" onClick={() => setMode('choose')}>
              ← 换一种诊断方式（一段话描述 / 直接跳过）
            </button>
          </div>
        </>
      )}

      {/* ============ 入口②：一段话自述（复用 /profile/parse），自述·中置信 ============ */}
      {mode === 'selfreport' && (
        <SelfReportProfile onFinish={finishWithPortrait} onBack={() => setMode('choose')} />
      )}

      {/* ============ 次要入口：传统表单 + 材料上传（保留原 3 步流程） ============ */}
      {mode === 'form' && (
      <>
      <button className="pb-backto-dialogue" onClick={() => setMode('choose')}>
        ← 返回诊断入口
      </button>

      {/* 三步进度条 */}
      <div className="profile-builder__progress">
        {STEP_LABELS.map((label, index) => {
          const cur = STEP_INDEX[step]
          return (
            <div
              key={label}
              className={`profile-builder__step ${cur === index ? 'profile-builder__step--active' : ''} ${
                cur > index ? 'profile-builder__step--completed' : ''
              }`}
            >
              <div className="profile-builder__step-number">{index + 1}</div>
              <span className="profile-builder__step-label">{label}</span>
            </div>
          )
        })}
      </div>

      <AnimatePresence mode="wait">
        {/* ============ Step 1 信息采集 ============ */}
        {step === 'collect' && (
          <motion.div
            key="collect"
            className="profile-builder__step-content"
            variants={revealItem}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            {/* 多模态输入 */}
            <div className="profile-builder__card">
              <h2>信息采集</h2>
              <p>上传简历或截图、用文字描述你的背景，AI 将自动解析为结构化画像；也可手动填写兜底</p>

              {/* 上传区（简历 PDF/Word + 图片 OCR）*/}
              <div
                className={`pb-upload ${dragOver ? 'pb-upload--over' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragOver(false)
                  addFiles(e.dataTransfer.files)
                }}
                onClick={() => fileInputRef.current?.click()}
                role="button"
                tabIndex={0}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.doc,.docx,image/*"
                  className="pb-upload__input"
                  onChange={(e) => addFiles(e.target.files)}
                />
                <div className="pb-upload__icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="17 8 12 3 7 8" />
                    <line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                </div>
                <div className="pb-upload__text">
                  <strong>点击或拖拽上传</strong>
                  <span>简历 PDF / Word · 成绩单或证书截图（自动 OCR）</span>
                </div>
                <div className="pb-upload__modes">
                  <span className="pb-modechip">📄 简历解析</span>
                  <span className="pb-modechip">🖼️ 图片 OCR</span>
                  <span className="pb-modechip">📝 文字描述</span>
                </div>
              </div>

              {/* 已选文件 */}
              {files.length > 0 && (
                <div className="pb-files">
                  {files.map((f, i) => (
                    <div className="pb-file" key={`${f.name}-${i}`}>
                      <span className={`pb-file__kind pb-file__kind--${f.kind}`}>
                        {f.kind === 'image' ? '图' : '档'}
                      </span>
                      <span className="pb-file__name">{f.name}</span>
                      <button
                        className="pb-file__remove"
                        aria-label="移除"
                        onClick={(e) => {
                          e.stopPropagation()
                          setFiles((prev) => prev.filter((_, idx) => idx !== i))
                        }}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* 文字描述 */}
              <div className="form-group pb-desc">
                <label className="form-label">文字描述（选填）</label>
                <textarea
                  className="pb-textarea"
                  placeholder="例：人工智能硕士，做过 RAG 项目，熟悉 PyTorch 和 Prompt 工程，想转大模型应用方向…"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                />
              </div>
            </div>

            {/* 目标岗位 · 实时市场画像（迁移复用）*/}
            <div className="profile-builder__card profile-builder__card--job">
              <div className="pb-section-head">
                <h2>目标岗位 · 实时市场画像</h2>
                <span className="pb-badge">{jobOffline ? '离线快照 · 预置库' : '联网快照 · 缓存'}</span>
              </div>
              <p>选择目标岗位，其实时技能要求将作为下一步「岗位对标」的基准线</p>
              <JobMarketPanel onSelect={onSelectJob} onOffline={onJobOffline} />
            </div>

            {/* 操作区 */}
            <div className="profile-builder__actions profile-builder__actions--collect">
              <button className="profile-builder__back-btn" onClick={startManual}>
                跳过上传，手动填写
              </button>
              <div className="pb-collect-primary">
                {!targetJob && <span className="pb-hint">请先选择目标岗位</span>}
                <button
                  className="profile-builder__next-btn"
                  disabled={!targetJob || !hasInput}
                  onClick={runParse}
                >
                  智能解析 →
                </button>
              </div>
            </div>
          </motion.div>
        )}

        {/* ============ 解析中 ============ */}
        {step === 'parsing' && (
          <motion.div key="parsing" className="profile-builder__generating" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <div className="generating-animation">
              <motion.div
                className="generating-orb"
                animate={{ scale: [1, 1.2, 1], rotate: [0, 180, 360] }}
                transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
              />
              <motion.div
                className="generating-pulse"
                animate={{ scale: [1, 2], opacity: [0.5, 0] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
            </div>
            <h2>正在解析你的多模态信息…</h2>
            <p>学情诊断 Agent 正在从简历 / 图片 / 描述中抽取结构化能力画像</p>
          </motion.div>
        )}

        {/* ============ Step 2 确认画像 ============ */}
        {step === 'confirm' && draft && (
          <motion.div
            key="confirm"
            className="profile-builder__step-content"
            variants={revealItem}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            <div className="profile-builder__card">
              <div className="pb-section-head">
                <h2>确认画像</h2>
                <span className="pb-badge">解析结果 · 可编辑</span>
              </div>
              <p>核对 AI 解析出的画像，每项已标注来源；如有出入可直接修改，确认后写入你的学习者画像</p>

              {/* AI 画像综述：先综述、再核对结构化字段 */}
              {narrative ? (
                <div className="pb-narrative">
                  <div className="pb-narrative__head">
                    <span className="pb-narrative__icon">✦</span>
                    <span className="pb-narrative__title">AI 画像综述</span>
                    <span className="pb-narrative__count">综合 {narrative.materialCount} 份材料生成</span>
                  </div>

                  <div className="pb-narrative__body">
                    {narrative.paragraphs.map((segs, pi) => (
                      <p className="pb-narrative__para" key={pi}>
                        {segs.map((seg, si) =>
                          seg.tone ? (
                            <mark
                              key={si}
                              className={`pb-hl pb-hl--${seg.tone} ${
                                seg.sourceId ? 'pb-hl--linked' : ''
                              } ${seg.sourceId && seg.sourceId === activeSource ? 'pb-hl--active' : ''}`}
                              onClick={seg.sourceId ? () => setActiveSource(seg.sourceId!) : undefined}
                              title={seg.sourceId ? '点击回看来源' : undefined}
                            >
                              {seg.text}
                            </mark>
                          ) : (
                            <span key={si}>{seg.text}</span>
                          )
                        )}
                      </p>
                    ))}
                  </div>

                  {/* 信息来源 chips：可点击回看 */}
                  <div className="pb-narrative__sources">
                    <span className="pb-narrative__sources-label">信息来源</span>
                    {narrative.sources.map((src) => (
                      <button
                        key={src.id}
                        className={`pb-srcchip pb-srcchip--${src.kind} ${
                          activeSource === src.id ? 'pb-srcchip--active' : ''
                        }`}
                        onClick={() => setActiveSource(activeSource === src.id ? null : src.id)}
                      >
                        <span className="pb-srcchip__kind">
                          {src.kind === 'image' ? '🖼️' : src.kind === 'text' ? '📝' : '📄'}
                        </span>
                        {src.label}
                      </button>
                    ))}
                  </div>

                  {activeSourceLabel && (
                    <div className="pb-narrative__viewer">
                      正在回看来源：<strong>{activeSourceLabel}</strong>
                      <button className="pb-narrative__viewer-close" onClick={() => setActiveSource(null)}>
                        收起
                      </button>
                    </div>
                  )}

                  <p className="pb-narrative__note">
                    综述严格基于上传材料生成，不含材料外信息；关键结论可点击高亮或来源 chip 回看核对。
                  </p>
                </div>
              ) : (
                <div className="pb-narrative pb-narrative--manual">
                  <span className="pb-narrative__icon">✎</span>
                  <span>本画像为手动填写，未上传材料，故不生成 AI 综述。请直接核对下方字段。</span>
                </div>
              )}

              {/* 基本信息（可编辑 chip 组）*/}
              <div className="form-group">
                <label className="form-label">
                  学历背景 <SourceTag source={draft.education.source} />
                </label>
                <div className="form-options">
                  {educationLevels.map((level) => (
                    <button
                      key={level}
                      className={`form-option ${draft.education.value === level ? 'form-option--selected' : ''}`}
                      onClick={() => updateBasic('education', level)}
                    >
                      {level}
                    </button>
                  ))}
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">
                  专业方向 <SourceTag source={draft.major.source} />
                </label>
                <div className="form-options">
                  {majorFields.map((field) => (
                    <button
                      key={field}
                      className={`form-option ${draft.major.value === field ? 'form-option--selected' : ''}`}
                      onClick={() => updateBasic('major', field)}
                    >
                      {field}
                    </button>
                  ))}
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">
                  学习目标 <SourceTag source={draft.goal.source} />
                </label>
                <div className="form-options">
                  {learningGoals.map((goal) => (
                    <button
                      key={goal}
                      className={`form-option ${draft.goal.value === goal ? 'form-option--selected' : ''}`}
                      onClick={() => updateBasic('goal', goal)}
                    >
                      {goal}
                    </button>
                  ))}
                </div>
              </div>

              {/* 能力维度（可编辑滑块）*/}
              <div className="form-group">
                <label className="form-label">能力维度自评（解析估值，可微调）</label>
                <div className="pb-skills">
                  {draft.skills.map((s) => (
                    <div className="pb-skill" key={s.name}>
                      <div className="pb-skill__head">
                        <span className="pb-skill__name">{s.name}</span>
                        <SourceTag source={s.source} />
                        <span className="pb-skill__val">{s.level}</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={s.level}
                        className="knowledge-slider"
                        onChange={(e) => updateSkill(s.name, parseInt(e.target.value))}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="profile-builder__actions">
                <button className="profile-builder__back-btn" onClick={() => setStep('collect')}>
                  返回采集
                </button>
                <button
                  className="profile-builder__next-btn"
                  disabled={!draft.education.value || !draft.major.value || !draft.goal.value}
                  onClick={() => setStep('benchmark')}
                >
                  确认画像，查看岗位对标 →
                </button>
              </div>
            </div>
          </motion.div>
        )}

        {/* ============ Step 3 岗位对标 ============ */}
        {step === 'benchmark' && draft && (
          <motion.div
            key="benchmark"
            className="profile-builder__step-content"
            variants={revealItem}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            <div className="profile-builder__card">
              <div className="pb-section-head">
                <h2>岗位对标</h2>
                {targetJob ? (
                  <span className="pb-badge">
                    对标 {targetJob.name} · 达标 {metCount}/{RADAR_DIMS.length}
                  </span>
                ) : (
                  <span className="pb-badge">未选目标岗位</span>
                )}
              </div>
              <p>雷达图叠加「当前能力 vs 该岗位实时技能要求」，逐维度给出差距分档</p>

              {/* 雷达：当前能力 + 岗位要求线 */}
              <div className="pb-radar">
                <RadarChart
                  data={{ dimensions: [...RADAR_DIMS], values: currentValues }}
                  target={radarTarget}
                  targetName={targetJob ? `${targetJob.name}·要求` : undefined}
                />
              </div>

              {/* 对标摘要 */}
              {targetJob && (
                <div className="pb-match">
                  <div className="pb-match__stat">
                    <CountUp className="pb-match__value" value={matchPct} suffix="%" />
                    <span className="pb-match__label">岗位匹配度</span>
                  </div>
                  <div className="pb-match__legend">
                    <span className="pb-tier-dot pb-tier-dot--met" /> 已具备
                    <span className="pb-tier-dot pb-tier-dot--partial" /> 部分掌握
                    <span className="pb-tier-dot pb-tier-dot--missing" /> 缺失
                  </div>
                </div>
              )}

              {/* Gap 三档列表 */}
              <div className="pb-gaps">
                {gaps.map((g) => (
                  <div className={`pb-gap pb-gap--${TIER_META[g.tier].cls}`} key={g.dim}>
                    <div className="pb-gap__top">
                      <span className="pb-gap__name">{g.dim}</span>
                      <span className={`pb-gap__tier pb-gap__tier--${TIER_META[g.tier].cls}`}>
                        {TIER_META[g.tier].label}
                      </span>
                    </div>
                    <div className="pb-gap__bar">
                      <span className="pb-gap__fill" style={{ width: `${g.current}%` }} />
                      {targetJob && <span className="pb-gap__req" style={{ left: `${g.required}%` }} title={`要求 ${g.required}`} />}
                    </div>
                    <div className="pb-gap__meta">
                      <span>当前 {g.current}</span>
                      {targetJob && <span>要求 {g.required}{g.gap > 0 ? ` · 差 ${g.gap}` : ''}</span>}
                    </div>
                  </div>
                ))}
              </div>

              <div className="profile-builder__actions">
                <button className="profile-builder__back-btn" onClick={() => setStep('confirm')}>
                  返回确认
                </button>
                <button className="profile-builder__next-btn" onClick={() => finish()}>
                  生成针对性学习路径 →
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      </>
      )}
    </div>
  )
}

/** 来源徽章：标注画像每项数据的多模态来源 */
function SourceTag({ source }: { source: Source }) {
  const m = SOURCE_META[source]
  return <span className={`pb-srctag pb-srctag--${m.tone}`}>{m.label}</span>
}
