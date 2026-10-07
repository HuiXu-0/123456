import { useEffect, useRef, useState, lazy, Suspense } from 'react'
import {
  getCornellNote,
  saveCornellNote,
  outlineToMarkdown,
  type CornellCues,
  type CornellNote,
} from '../services/learningFlow'

const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'))

/**
 * 康奈尔笔记三栏面板（18.1 预填 + 18.4 读 + 18.5 防抖保存）。
 * - 线索栏：18.1 cues（问题/关键词），每条可作答（复述自测）。
 * - 主笔记：Markdown 自由编辑，初值由 18.1 noteOutline 预填；可切换预览。
 * - 总结区：18.1 summaryHint 作为引导占位，用户写自己的话。
 * 编辑后防抖 900ms 调 18.5 整体覆盖保存，避免频繁写；卸载前 flush 未保存内容。
 */
interface CornellNotesProps {
  kpId: string
  cues: CornellCues | null
  /** 每次保存成功回调（父级据此置 18.3 note=true）。 */
  onSaved?: (note: CornellNote) => void
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error'
const SAVE_DEBOUNCE_MS = 900

const fmtTime = (iso: string) => {
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export default function CornellNotes({ kpId, cues, onSaved }: CornellNotesProps) {
  const [cueNotes, setCueNotes] = useState<Record<string, string>>({})
  const [mainNotes, setMainNotes] = useState('')
  const [summary, setSummary] = useState('')
  const [preview, setPreview] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [savedAt, setSavedAt] = useState<string>('')
  const [loaded, setLoaded] = useState(false)

  const timerRef = useRef<number | null>(null)
  // 最新草稿镜像：供防抖/卸载 flush 读取，避免闭包拿到旧值
  const draftRef = useRef({ cueNotes, mainNotes, summary })
  draftRef.current = { cueNotes, mainNotes, summary }

  /* 载入：18.4 读已存笔记；主笔记为空时用 18.1 noteOutline 预填，cueNotes 按 cueId 回填 */
  useEffect(() => {
    let alive = true
    getCornellNote(kpId)
      .then((note) => {
        if (!alive) return
        const cn: Record<string, string> = {}
        for (const c of note.cueNotes) cn[c.cueId] = c.text
        setCueNotes(cn)
        setMainNotes(note.mainNotes || (cues ? outlineToMarkdown(cues.noteOutline) : ''))
        setSummary(note.summary)
        if (note.mainNotes || note.summary || note.cueNotes.length) setSavedAt(note.updatedAt)
        setLoaded(true)
      })
      .catch((e) => {
        console.error('[cornell] 读取笔记失败', e)
        setMainNotes(cues ? outlineToMarkdown(cues.noteOutline) : '')
        setLoaded(true)
      })
    return () => {
      alive = false
    }
    // 仅挂载时载入一次（kpId / cues 在面板停留期内稳定）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 立即保存（供防抖回调与卸载 flush 复用）。 */
  const flush = () => {
    const { cueNotes: cn, mainNotes: mn, summary: sm } = draftRef.current
    setSaveState('saving')
    saveCornellNote(kpId, {
      cueNotes: Object.entries(cn)
        .filter(([, text]) => text.trim())
        .map(([cueId, text]) => ({ cueId, text })),
      mainNotes: mn,
      summary: sm,
    })
      .then((note) => {
        setSaveState('saved')
        setSavedAt(note.updatedAt)
        onSaved?.(note)
      })
      .catch((e) => {
        console.error('[cornell] 保存笔记失败', e)
        setSaveState('error')
      })
  }

  /** 任一字段变更 → 防抖排程一次保存。 */
  const scheduleSave = () => {
    if (timerRef.current) window.clearTimeout(timerRef.current)
    setSaveState('saving')
    timerRef.current = window.setTimeout(flush, SAVE_DEBOUNCE_MS)
  }

  /* 卸载：清定时器并 flush 一次未落盘的草稿（仅在已载入且有内容时） */
  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current)
      const { mainNotes: mn, summary: sm, cueNotes: cn } = draftRef.current
      if (mn || sm || Object.keys(cn).length) flush()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onCue = (cueId: string, text: string) => {
    setCueNotes((m) => ({ ...m, [cueId]: text }))
    scheduleSave()
  }

  const saveLabel =
    saveState === 'saving'
      ? '保存中…'
      : saveState === 'error'
        ? '保存失败 · 将自动重试'
        : savedAt
          ? `已保存 · ${fmtTime(savedAt)}`
          : '编辑后自动保存'

  if (!loaded) return <div className="cornell__loading">康奈尔笔记加载中…</div>

  return (
    <div className="cornell">
      <div className="cornell__bar">
        <span className="cornell__bar-title">📝 康奈尔笔记法</span>
        <span className={`cornell__save cornell__save--${saveState}`}>
          <span className="cornell__save-dot" />
          {saveLabel}
        </span>
      </div>

      <div className="cornell__grid">
        {/* 线索栏（左）：18.1 cues + 复述自测作答 */}
        <aside className="cornell__cues">
          <div className="cornell__col-head">
            <span className="cornell__col-name">线索栏</span>
            <span className="cornell__col-hint">带着问题复述 · 自测</span>
          </div>
          {cues && cues.cues.length ? (
            <ul className="cornell__cue-list">
              {cues.cues.map((c) => (
                <li key={c.id} className="cornell__cue">
                  <div className="cornell__cue-head">
                    <span className={`cornell__cue-tag cornell__cue-tag--${c.type}`}>
                      {c.type === 'question' ? '问' : '词'}
                    </span>
                    <span className="cornell__cue-text">{c.text}</span>
                  </div>
                  <input
                    className="cornell__cue-input"
                    placeholder="用自己的话回答…"
                    value={cueNotes[c.id] ?? ''}
                    onChange={(e) => onCue(c.id, e.target.value)}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className="cornell__empty">线索生成中…</p>
          )}
        </aside>

        {/* 主笔记区（右）：18.1 noteOutline 预填，可编辑/预览 */}
        <section className="cornell__main">
          <div className="cornell__col-head">
            <span className="cornell__col-name">主笔记区</span>
            <button
              type="button"
              className="cornell__toggle"
              onClick={() => setPreview((p) => !p)}
            >
              {preview ? '✎ 编辑' : '👁 预览'}
            </button>
          </div>
          {preview ? (
            <div className="cornell__preview">
              <Suspense fallback={<div className="cornell__loading">渲染中…</div>}>
                {mainNotes.trim() ? (
                  <MarkdownRenderer content={mainNotes} />
                ) : (
                  <p className="cornell__empty">还没有笔记，切回编辑开始记录。</p>
                )}
              </Suspense>
            </div>
          ) : (
            <textarea
              className="cornell__textarea"
              value={mainNotes}
              placeholder="支持 Markdown：用 ## 标题、- 列表 整理你的理解…"
              onChange={(e) => {
                setMainNotes(e.target.value)
                scheduleSave()
              }}
            />
          )}
        </section>
      </div>

      {/* 总结区（底，整行）：18.1 summaryHint 引导 */}
      <section className="cornell__summary">
        <div className="cornell__col-head">
          <span className="cornell__col-name">总结区</span>
          <span className="cornell__col-hint">合上资料，用一两句话复述</span>
        </div>
        <textarea
          className="cornell__textarea cornell__textarea--summary"
          value={summary}
          placeholder={cues?.summaryHint || '用自己的话概括这个知识点的核心…'}
          onChange={(e) => {
            setSummary(e.target.value)
            scheduleSave()
          }}
        />
      </section>
    </div>
  )
}
