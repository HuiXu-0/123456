import { useEffect, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import PageHeader from '../components/PageHeader'
import { RevealGroup, RevealItem } from '../components/Reveal'
import ResourceLibraryPreview from '../components/ResourceLibraryPreview'
import ResourceTypeIcon from '../components/ResourceTypeIcon'
import { setResourceNav } from '../services/resourceNav'
import {
  deleteResource,
  getResourceHistory,
  HISTORY_KPS,
  KIND_LABEL,
  type ResourceHistoryItem,
  type ResourceKind,
} from '../services/resourceHistory'
import type { PageType } from '../App'
import './MyResourceLibrary.css'

/* 形态 → 主题强调色（卡片左缘/难度徽标等仍用；类型图标统一走 <ResourceTypeIcon>） */
const KIND_ACCENT: Record<ResourceKind, string> = {
  lecture: '#5b7f6e',
  video: '#3f6cc4',
  diagram: '#c58940',
  mindmap: '#7c5cc4',
  code: '#5a6472',
  quiz: '#c2557a',
  flashcard: '#2f9e8f',
}

/* 形态筛选顺序（含「全部」；quiz/flashcard 为文档学习产出形态） */
const KIND_FILTERS: { key: ResourceKind | ''; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'lecture', label: '讲义' },
  { key: 'video', label: '视频' },
  { key: 'diagram', label: '图解' },
  { key: 'mindmap', label: '导图' },
  { key: 'code', label: '代码' },
  { key: 'quiz', label: '练习题' },
  { key: 'flashcard', label: '闪卡' },
]

/* 时间快捷筛选 → 起始时间（ISO）。'' = 不限 */
type TimeKey = '' | 'today' | '7d' | '30d'
const TIME_FILTERS: { key: TimeKey; label: string }[] = [
  { key: '', label: '全部时间' },
  { key: 'today', label: '今天' },
  { key: '7d', label: '近 7 天' },
  { key: '30d', label: '近 30 天' },
]
function timeToStart(key: TimeKey): string | undefined {
  if (!key) return undefined
  const now = new Date()
  if (key === 'today') {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    return d.toISOString()
  }
  const days = key === '7d' ? 7 : 30
  return new Date(now.getTime() - days * 86400_000).toISOString()
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 具体日期 */
function relTime(iso: string): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  return new Date(t).toLocaleDateString('zh-CN')
}

/* 形态 → 学习资源页落点 Tab（「在学习页打开」用，仅内置课程 kp 资源）。
   文档学习特有的 quiz/flashcard 在学习资源页无对应 Tab，回落 lecture（文档资源改跳文档学习页，见 openInLearning）。 */
const KIND_TO_TAB: Record<ResourceKind, string> = {
  lecture: 'lecture',
  video: 'video',
  diagram: 'diagram',
  mindmap: 'mindmap',
  code: 'code',
  quiz: 'lecture',
  flashcard: 'lecture',
}

export default function MyResourceLibrary({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  const [kindFilter, setKindFilter] = useState<ResourceKind | ''>('')
  const [kpFilter, setKpFilter] = useState<string>('')
  const [timeFilter, setTimeFilter] = useState<TimeKey>('')
  const [records, setRecords] = useState<ResourceHistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [preview, setPreview] = useState<ResourceHistoryItem | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(false)
    getResourceHistory({
      pageSize: 100,
      kind: kindFilter || undefined,
      kpId: kpFilter || undefined,
      startTime: timeToStart(timeFilter),
    })
      .then((res) => {
        if (!alive) return
        setRecords(res.items)
        setTotal(res.total)
      })
      .catch((e) => {
        console.error('[resource-library] 加载生成历史失败', e)
        if (alive) setError(true)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [kindFilter, kpFilter, timeFilter])

  const hasFilter = !!(kindFilter || kpFilter || timeFilter)

  /* 「在学习页打开」：文档学习资源 → 跳文档学习页；内置课程资源 → 设置资源页落点后跳学习资源页 */
  const openInLearning = (r: ResourceHistoryItem) => {
    setPreview(null)
    if (r.source === 'document') {
      onNavigate?.('document-learning')
      return
    }
    setResourceNav(r.kpId, 'browse', KIND_TO_TAB[r.kind])
    onNavigate?.('learning-resource')
  }

  /* 删除资产（CRUD·删）：二次确认 → 后端按 user 校验归属删除（连带产物）→ 乐观移除刷新列表。 */
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const handleDelete = async (r: ResourceHistoryItem) => {
    if (deletingId) return
    const ok = window.confirm(
      `确定删除「${KIND_LABEL[r.kind]} · ${r.kpName}」吗？\n此操作不可撤销，将连同已生成产物一并移除。`
    )
    if (!ok) return
    setDeletingId(r.id)
    try {
      await deleteResource(r.id)
      if (preview?.id === r.id) setPreview(null)
      setRecords((rs) => rs.filter((x) => x.id !== r.id))
      setTotal((t) => Math.max(0, t - 1))
    } catch (e) {
      console.error('[resource-library] 删除失败', e)
      window.alert('删除失败：可能无权操作或该资源已不存在，请刷新后重试。')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="reslib-page">
      <PageHeader
        title="我的资源库"
        highlight="资源库"
        subtitle="你生成过的全部学习资产 · 讲义 / 视频 / 图解 / 思维导图 / 代码 · 可查看与下载"
        crumb="我的资源库"
        onBack={() => onNavigate?.('dashboard')}
        badges={[
          { label: '资产总数', value: total },
          { label: '当前展示', value: records.length },
        ]}
      />

      {/* 筛选区：形态 chips + 知识点下拉 + 时间快捷（reslib-body 负责筛选区与网格间距） */}
      <RevealGroup className="reslib-body">
        <RevealItem className="reslib-filters">
          <div className="reslib-filters__row">
            <span className="reslib-filters__label">类型</span>
            <div className="reslib-chips">
              {KIND_FILTERS.map((f) => (
                <button
                  key={f.key || 'all'}
                  type="button"
                  className={`reslib-chip ${kindFilter === f.key ? 'reslib-chip--active' : ''}`}
                  onClick={() => setKindFilter(f.key)}
                >
                  {f.key && <ResourceTypeIcon kind={f.key} size={16} className="reslib-chip__icon" />}
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          <div className="reslib-filters__row">
            <span className="reslib-filters__label">知识点</span>
            <select
              className="reslib-select"
              value={kpFilter}
              onChange={(e) => setKpFilter(e.target.value)}
            >
              <option value="">全部知识点</option>
              {HISTORY_KPS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                </option>
              ))}
            </select>

            <span className="reslib-filters__label reslib-filters__label--gap">时间</span>
            <div className="reslib-chips">
              {TIME_FILTERS.map((f) => (
                <button
                  key={f.key || 'all-time'}
                  type="button"
                  className={`reslib-chip ${timeFilter === f.key ? 'reslib-chip--active' : ''}`}
                  onClick={() => setTimeFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>

            {hasFilter && (
              <button
                type="button"
                className="reslib-clear"
                onClick={() => {
                  setKindFilter('')
                  setKpFilter('')
                  setTimeFilter('')
                }}
              >
                清除筛选
              </button>
            )}
          </div>
        </RevealItem>

        {/* 内容区：加载 / 错误 / 空态 / 资产网格 */}
        {loading ? (
          <RevealItem className="reslib-state">
            <div className="reslib-state__spinner" />
            <p className="reslib-state__text">正在载入你的资源库…</p>
          </RevealItem>
        ) : error ? (
          <RevealItem className="reslib-state">
            <div className="reslib-state__icon">😵</div>
            <p className="reslib-state__text">加载失败，请稍后重试。</p>
          </RevealItem>
        ) : records.length === 0 ? (
          <RevealItem className="reslib-state reslib-empty">
            <div className="reslib-state__icon">🗂️</div>
            <h3 className="reslib-empty__title">{hasFilter ? '没有符合筛选条件的资源' : '资源库还是空的'}</h3>
            <p className="reslib-state__text">
              {hasFilter
                ? '换个类型 / 知识点 / 时间试试，或清除筛选查看全部。'
                : '去「学习资源」生成讲义、视频、图解、思维导图或代码，生成的资产会自动收进这里。'}
            </p>
            {hasFilter ? (
              <button
                type="button"
                className="reslib-empty__cta"
                onClick={() => {
                  setKindFilter('')
                  setKpFilter('')
                  setTimeFilter('')
                }}
              >
                清除筛选
              </button>
            ) : (
              <button
                type="button"
                className="reslib-empty__cta"
                onClick={() => onNavigate?.('learning-resource')}
              >
                去生成学习资源 →
              </button>
            )}
          </RevealItem>
        ) : (
          <>
            {/* 资产网格（筛选器下方直接是卡片网格） */}
            <RevealItem className="reslib-grid">
              {records.map((r) => (
                <article
                  key={r.id}
                  className="reslib-card"
                  style={{ ['--kind-accent' as string]: KIND_ACCENT[r.kind] }}
                >
                  <button
                    type="button"
                    className="reslib-card__main"
                    onClick={() => setPreview(r)}
                    title="查看该资源"
                  >
                    <span className="reslib-card__icon">
                      <ResourceTypeIcon kind={r.kind} size={48} />
                    </span>
                    <span className="reslib-card__body">
                      <span className="reslib-card__kind">{KIND_LABEL[r.kind]}</span>
                      <span className="reslib-card__kp">{r.kpName}</span>
                      <span className="reslib-card__metaline">
                        {r.difficulty && <span className="reslib-card__diff">{r.difficulty}</span>}
                        <span className="reslib-card__time">{relTime(r.createdAt)}</span>
                      </span>
                    </span>
                  </button>
                  <div className="reslib-card__actions">
                    <button type="button" className="reslib-card__btn" onClick={() => setPreview(r)}>
                      查看
                    </button>
                    <button
                      type="button"
                      className="reslib-card__btn reslib-card__btn--primary"
                      onClick={() => setPreview(r)}
                      title="在预览中下载（讲义 md/pdf · 视频 mp4 · 图解/导图 svg/png · 代码文件）"
                    >
                      下载
                    </button>
                    <button
                      type="button"
                      className="reslib-card__btn reslib-card__btn--danger"
                      onClick={() => void handleDelete(r)}
                      disabled={deletingId === r.id}
                      title="删除该资产（连同已生成产物，不可撤销）"
                    >
                      {deletingId === r.id ? '删除中…' : '删除'}
                    </button>
                  </div>
                </article>
              ))}
            </RevealItem>
          </>
        )}
      </RevealGroup>

      <AnimatePresence>
        {preview && (
          <ResourceLibraryPreview
            record={preview}
            onClose={() => setPreview(null)}
            onOpenInLearning={openInLearning}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
