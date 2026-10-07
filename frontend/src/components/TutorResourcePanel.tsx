import { lazy, Suspense, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import MarkdownRenderer from './MarkdownRenderer'
import type { ExternalResource } from './ResourceAggregator'
import { StatusChip, type ItemStatus } from './genStatus'
import {
  generateResources,
  fetchRecommendations,
  type RemedialSuggestResult,
  type RemedialResource,
  type RemedialType,
} from '../services/tutorResource'
import './TutorResourcePanel.css'

const MermaidDiagram = lazy(() => import('./MermaidDiagram'))
const VideoLecture = lazy(() => import('./VideoLecture'))

/**
 * 智能辅导·资源生成清单 + 按需逐项生成（接口文档 8.8 + 8.6 增量）。
 *
 * 改动（问题 5 / 问题 6）：
 *  - 勾选要生成的内容类型后，按勾选「逐项」生成（逐个调用既有单类型接口），
 *    每项有独立状态（待生成/生成中/已完成/失败）+ 顶部总进度，生成完一项即展示一项；
 *  - 「资源推荐」（联网聚合的外部资源）作为成果项之一并入，与讲义/图解等并列，可打开。
 *  后端生成逻辑不变；此处仅把「批量等待」改为「按选择 + 逐项进度 + 逐项呈现」。
 */

/** 面板内可生成项 = 后端补救资源类型 + 外部资源推荐 */
type PanelType = RemedialType | 'external'

const TYPE_ICON: Record<PanelType, string> = {
  diagram: '🗺️',
  example: '📝',
  video: '🎬',
  lecture: '📖',
  external: '🔗',
}

const EXTERNAL_TYPE_ICON: Record<ExternalResource['type'], string> = {
  视频: '🎬',
  论文: '📄',
  文档: '📘',
  课程: '🎓',
}

interface Props {
  suggestion: RemedialSuggestResult
  kpName?: string
  /** 生成完成回调：传递完整生成结果（含资源内容 + 外部推荐） */
  onGenerated?: (result: { remedial: Partial<Record<PanelType, RemedialResource>>; external: ExternalResource[] | null }) => void
}

interface PanelOption {
  type: PanelType
  title: string
  expect: string
}

export default function TutorResourcePanel({ suggestion, kpName, onGenerated }: Props) {
  /* 可勾选清单：后端识别的补救资源 + 始终追加「资源推荐」一项（问题 6） */
  const options = useMemo<PanelOption[]>(
    () => [
      ...suggestion.suggestions.map((s) => ({ type: s.type as PanelType, title: s.title, expect: s.expect })),
      {
        type: 'external' as PanelType,
        title: `资源推荐 · ${suggestion.problemPoint}`,
        expect: 'AI 联网聚合的优质外部资源（视频 / 课程 / 论文 / 文档），可直接打开学习',
      },
    ],
    [suggestion]
  )

  const [picked, setPicked] = useState<Set<PanelType>>(() => new Set(options.map((o) => o.type)))
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState<Partial<Record<PanelType, ItemStatus>>>({})
  const [remedial, setRemedial] = useState<Partial<Record<PanelType, RemedialResource>>>({})
  const [external, setExternal] = useState<ExternalResource[] | null>(null)
  const resultsRef = useRef<{ remedial: Partial<Record<PanelType, RemedialResource>>; external: ExternalResource[] | null }>({ remedial: {}, external: null })

  const toggle = (t: PanelType) =>
    setPicked((prev) => {
      const next = new Set(prev)
      next.has(t) ? next.delete(t) : next.add(t)
      return next
    })

  /* 逐项生成：按勾选顺序依次调用既有单类型接口，每项 running→done/error，逐项落地展示。 */
  const generate = async () => {
    if (!picked.size || running) return
    const chosen = options.filter((o) => picked.has(o.type)).map((o) => o.type)
    setRunning(true)
    setStatus(Object.fromEntries(chosen.map((t) => [t, 'pending'])) as Record<PanelType, ItemStatus>)
    setRemedial({})
    setExternal(null)
    resultsRef.current = { remedial: {}, external: null }

    for (const t of chosen) {
      setStatus((prev) => ({ ...prev, [t]: 'running' }))
      try {
        if (t === 'external') {
          const items = await fetchRecommendations(
            suggestion.kpId,
            suggestion.problemPoint,
            kpName ?? suggestion.kpName
          )
          setExternal(items)
          resultsRef.current.external = items
        } else {
          const res = await generateResources(
            suggestion.kpId,
            suggestion.problemPoint,
            [t],
            kpName ?? suggestion.kpName
          )
          const one = res.results[0]
          if (one) {
            setRemedial((prev) => ({ ...prev, [t]: one }))
            resultsRef.current.remedial[t] = one
          }
        }
        setStatus((prev) => ({ ...prev, [t]: 'done' }))
      } catch (e) {
        console.error(`[tutor-resource] 生成「${t}」失败`, e)
        setStatus((prev) => ({ ...prev, [t]: 'error' }))
      }
    }
    setRunning(false)
    onGenerated?.({ remedial: resultsRef.current.remedial, external: resultsRef.current.external })
  }

  const chosenList = options.filter((o) => picked.has(o.type))
  const startedTypes = options.filter((o) => status[o.type])
  const doneCount = startedTypes.filter((o) => status[o.type] === 'done' || status[o.type] === 'error').length
  const total = startedTypes.length
  const progress = total ? Math.round((doneCount / total) * 100) : 0
  const hasStarted = total > 0

  return (
    <motion.div
      className="trp"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <div className="trp__head">
        <span className="trp__badge">✦ 智能辅导</span>
        <span className="trp__point">
          识别到你的问题点：<strong>{suggestion.problemPoint}</strong>
        </span>
      </div>
      <p className="trp__hint">勾选要生成的内容类型，按选择「逐项」生成 —— 生成完一项即展示一项，无需等全部完成：</p>

      <div className="trp__list">
        {options.map((s) => (
          <label key={s.type} className={`trp__item ${picked.has(s.type) ? 'is-picked' : ''}`}>
            <input
              type="checkbox"
              checked={picked.has(s.type)}
              disabled={running}
              onChange={() => toggle(s.type)}
            />
            <span className="trp__item-icon">{TYPE_ICON[s.type]}</span>
            <span className="trp__item-body">
              <span className="trp__item-title">{s.title}</span>
              <span className="trp__item-expect">{s.expect}</span>
            </span>
            {status[s.type] && <StatusChip status={status[s.type]!} />}
          </label>
        ))}
      </div>

      <button className="trp__gen" disabled={!picked.size || running} onClick={generate}>
        {running
          ? `正在逐项生成…（${doneCount}/${total}）`
          : hasStarted
            ? `重新生成所选（${picked.size}）`
            : `生成所选（${picked.size}）`}
      </button>

      {/* 总进度条 */}
      {hasStarted && (
        <div className="trp__progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <span className="trp__progress-fill" style={{ width: `${progress}%` }} />
          <span className="trp__progress-text">{running ? `生成中 ${doneCount}/${total}` : `已完成 ${doneCount}/${total}`}</span>
        </div>
      )}

      {/* 生成成果：按勾选顺序逐项呈现（讲义/图解/视频/例题 + 资源推荐并列） */}
      <AnimatePresence>
        {hasStarted && (
          <motion.div
            className="trp__results"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {chosenList.map((o) => {
              const st = status[o.type]
              if (!st) return null
              return (
                <motion.div
                  key={o.type}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.25 }}
                >
                  {st === 'pending' || st === 'running' ? (
                    <div className="trp__result trp__result--pending">
                      <div className="trp__result-head">
                        <span className="trp__result-icon">{TYPE_ICON[o.type]}</span>
                        <span className="trp__result-title">{o.title}</span>
                        <StatusChip status={st} />
                      </div>
                      <div className="trp__skeleton">
                        <span className="trp__skeleton-bar" />
                        <span className="trp__skeleton-bar" />
                        <span className="trp__skeleton-bar trp__skeleton-bar--short" />
                      </div>
                    </div>
                  ) : st === 'error' ? (
                    <div className="trp__result trp__result--error">
                      <div className="trp__result-head">
                        <span className="trp__result-icon">{TYPE_ICON[o.type]}</span>
                        <span className="trp__result-title">{o.title}</span>
                        <StatusChip status={st} />
                      </div>
                      <p className="trp__error-text">该项生成失败，可重新生成所选重试。</p>
                    </div>
                  ) : o.type === 'external' ? (
                    <ExternalResult title={o.title} items={external ?? []} />
                  ) : (
                    remedial[o.type] && <ResourceResult resource={remedial[o.type]!} />
                  )}
                </motion.div>
              )
            })}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

/** 资源推荐成果项：联网聚合的外部资源，与生成产物并列，可打开 / 内嵌观看。 */
function ExternalResult({ title, items }: { title: string; items: ExternalResource[] }) {
  return (
    <div className="trp__result">
      <div className="trp__result-head">
        <span className="trp__result-icon">{TYPE_ICON.external}</span>
        <span className="trp__result-title">{title}</span>
      </div>
      {items.length === 0 ? (
        <p className="trp__error-text">暂无推荐资源。</p>
      ) : (
        <ul className="trp__rec-list">
          {items.map((r) => (
            <li key={r.id} className="trp__rec">
              <div className="trp__rec-main">
                <span className="trp__rec-type">{EXTERNAL_TYPE_ICON[r.type]} {r.type}</span>
                <span className="trp__rec-title">{r.title}</span>
                <span className="trp__rec-source">{r.source}{r.duration ? ` · ${r.duration}` : ''}</span>
                <span className="trp__rec-reason">💡 {r.reason}</span>
              </div>
              <a className="trp__rec-open" href={r.url} target="_blank" rel="noreferrer">
                ↗ 打开
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ResourceResult({ resource: r }: { resource: RemedialResource }) {
  const [showSolution, setShowSolution] = useState(false)
  return (
    <div className="trp__result">
      <div className="trp__result-head">
        <span className="trp__result-icon">{TYPE_ICON[r.type]}</span>
        <span className="trp__result-title">{r.title}</span>
      </div>

      {r.type === 'diagram' && r.mermaid && (
        <Suspense fallback={<div className="trp__loading">图解渲染中…</div>}>
          <MermaidDiagram chart={r.mermaid} />
        </Suspense>
      )}

      {r.type === 'lecture' && r.markdown && (
        <div className="trp__md">
          <MarkdownRenderer content={r.markdown} />
        </div>
      )}

      {r.type === 'example' && (
        <div className="trp__example">
          <p className="trp__example-q">{r.statement}</p>
          {!showSolution ? (
            <button className="trp__example-toggle" onClick={() => setShowSolution(true)}>
              查看解析 →
            </button>
          ) : (
            <pre className="trp__example-a">{r.solution}</pre>
          )}
        </div>
      )}

      {r.type === 'video' && r.scenes && (
        /* 复用主资源生成的 Remotion 播放组件：把已生成的分镜脚本喂进参数化模板渲染 + 同步旁白，
           与「学习资源页·讲解视频卡」表现一致（不再只渲染脚本文本）。 */
        <Suspense fallback={<div className="trp__loading">讲解视频渲染中…</div>}>
          <VideoLecture
            title={r.title}
            scenes={r.scenes.map((sc) => ({ title: sc.title, points: sc.points, narration: sc.narration }))}
          />
        </Suspense>
      )}
    </div>
  )
}
