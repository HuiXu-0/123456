import { useEffect, useRef, useState, lazy, Suspense } from 'react'
import { motion } from 'framer-motion'
import MarkdownRenderer from './MarkdownRenderer'
import QuizRenderer from './QuizRenderer'
import SourceTrace, { type SourceRef } from './SourceTrace'
import FlashcardDeck from './FlashcardDeck'
import { USE_REAL_API } from '../services/api'
import { getLecture, getDiagram } from '../services/resource'
import {
  generateLecture,
  generateVideo,
  generateDiagram,
  generateMindmap,
  generateQuiz,
  generateFlashcards,
} from '../services/documentLearning'
import { KP_RESOURCES, genericKpContent, kpCodeDemo } from '../data/kpResources'
import { ksPointById } from '../data/knowledgeSystem'
import { exportLectureMarkdown, exportLectureToPdf } from '../utils/lectureExport'
import { KIND_LABEL, type ResourceHistoryItem } from '../services/resourceHistory'
import '../pages/LearningResource.css'

/* 重型多模态组件按需懒加载（与学习资源页一致），保持资源库列表轻量 */
const MindMap = lazy(() => import('./MindMap'))
const MermaidDiagram = lazy(() => import('./MermaidDiagram'))
const VideoLecture = lazy(() => import('./VideoLecture'))
const CodeSandbox = lazy(() => import('./CodeSandbox'))

const Loading = () => <div className="resource-loading">资源加载中…</div>

/**
 * 文档资源内容缓存 + 在途去重。
 *
 * 「我的资源库」只存生成日志、不存产物，故查看文档资源需按来源文档**重建内容**（重型：RAG +
 * LLM 生成，联调下每次 ~数十秒）。而 React 严格模式会对同一 effect **双触发**、反复开关弹层也会
 * 重复重建——此前每次「查看」都会向后端并发发起**两次**相同的重型生成，多个叠加会把后端拖到
 * 无法响应（表现为「服务挂掉」）。这里按 (docId|kind|difficulty) **合并在途请求 + 缓存结果**：
 * 同一资源并发只打后端一次、再次查看直接命中缓存，彻底消除重复重建。失败不缓存以允许重试。
 */
const docContentCache = new Map<string, Promise<unknown>>()
function loadDocContent(kind: ResourceHistoryItem['kind'], docId: string, diff: string): Promise<unknown> {
  const key = `${docId}|${kind}|${diff}`
  let p = docContentCache.get(key)
  if (!p) {
    p = (async () => {
      if (kind === 'lecture') return generateLecture(docId, diff)
      if (kind === 'video') return generateVideo(docId, diff)
      if (kind === 'diagram') return generateDiagram(docId)
      if (kind === 'mindmap') return generateMindmap(docId)
      if (kind === 'quiz') return generateQuiz(docId, 5)
      return generateFlashcards(docId, 8)
    })().catch((e) => {
      docContentCache.delete(key) // 失败不缓存，允许下次重试
      throw e
    })
    docContentCache.set(key, p)
  }
  return p
}

/** 从讲义 markdown 提取标题大纲（跳过代码块内 # 注释）→ 供思维导图结构化（与学习资源页同口径）。 */
function lectureOutline(md: string): string {
  let inFence = false
  const lines: string[] = []
  for (const line of md.split('\n')) {
    if (/^\s*```/.test(line)) {
      inFence = !inFence
      continue
    }
    if (!inFence && /^#{1,4}\s/.test(line)) lines.push(line)
  }
  return lines.join('\n')
}

/* 头部主题色沿用学习资源页的插画浅底，保持视觉一致 */
const KIND_THEME: Record<ResourceHistoryItem['kind'], string> = {
  lecture: '#e7f4ee',
  video: '#e8f0fb',
  mindmap: '#efeafb',
  diagram: '#fbf2e2',
  code: '#eceef3',
  quiz: '#fbeaf1',
  flashcard: '#e5f5f1',
}

/**
 * 我的资源库·资源预览弹层。
 *
 * 复用学习资源页的多模态组件与导出能力，在资源库内**直接查看 + 下载**：
 * - 讲义：MarkdownRenderer + 导出 Markdown / PDF（复用 lectureExport）；
 * - 视频：VideoLecture（服务端渲染 mp4 就绪即可下载，内置降级实时播放）；
 * - 图解/导图：MermaidDiagram / MindMap（内置下载 SVG / PNG）；
 * - 代码：CodeSandbox（内置下载代码文件）。
 * 数据源与学习资源页一致：联调取后端 /resource/*，mock 取本地 KP_RESOURCES。
 */
export default function ResourceLibraryPreview({
  record,
  onClose,
  onOpenInLearning,
}: {
  record: ResourceHistoryItem
  onClose: () => void
  onOpenInLearning: (record: ResourceHistoryItem) => void
}) {
  const { kind, kpId, kpName } = record
  // 讲义/视频用记录里的难度；图解/导图/代码无难度 → 取讲义结构化默认档
  const difficulty = record.difficulty || '初级'
  const lectureRef = useRef<HTMLDivElement>(null)

  /* 文档学习资源：按来源文档重建其真实产物内容（讲义/视频/图解/导图/练习题/闪卡），
     而非按 kpId 取内置课程内容——修复「查看全跳代码界面」+「下载名错乱」。 */
  const isDoc = record.source === 'document'
  const docId = record.docId || record.kpId
  const docName = record.docTitle || record.kpName

  const [lecture, setLecture] = useState<string>('')
  const [mindmap, setMindmap] = useState<string>('')
  const [diagram, setDiagram] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(
    !isDoc && (kind === 'lecture' || kind === 'mindmap' || kind === 'diagram')
  )
  /* 文档产物内容（联调经 /document/generate/* 按文档重建；各类型结构不同，用宽松类型承接）。 */
  const [docData, setDocData] = useState<Record<string, unknown> | null>(null)
  const [docLoading, setDocLoading] = useState<boolean>(isDoc)
  const [docErr, setDocErr] = useState<boolean>(false)

  /* Esc 关闭 + 锁背景滚动（与学习资源页详情一致） */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [onClose])

  /* 文档学习资源：按来源文档重建各形态内容（讲义/视频/图解/导图/练习题/闪卡）。 */
  useEffect(() => {
    if (!isDoc) return
    let alive = true
    setDocLoading(true)
    setDocErr(false)
    const diff = record.difficulty || '初级'
    async function loadDoc() {
      try {
        // 合并在途 + 缓存：严格模式双触发 / 反复查看只对后端发起一次重型生成
        const data = await loadDocContent(kind, docId, diff)
        if (alive) setDocData(data as Record<string, unknown>)
      } catch (e) {
        console.error('[resource-library] 文档资源内容加载失败', e)
        if (alive) setDocErr(true)
      } finally {
        if (alive) setDocLoading(false)
      }
    }
    void loadDoc()
    return () => {
      alive = false
    }
    // 记录不变；docId/kind/难度决定内容
  }, [isDoc, kind, docId, record.difficulty])

  /* 按形态取内容（内置课程资源：联调后端 / mock 本地）。文档学习资源由上面的 loadDoc 负责，这里跳过。 */
  useEffect(() => {
    if (isDoc) return
    let alive = true
    /* mock 内容包：核心点取精修内容；未收录（nn 精修常量在资源页内部、及全部体系点）
       回退按名称/简介参数化的通用模板，预览不再空白（联调走后端真实内容，不受影响）。 */
    const mock = KP_RESOURCES[kpId] ?? genericKpContent(kpName, ksPointById(kpId)?.description ?? '')
    async function load() {
      try {
        if (kind === 'lecture') {
          // 纯 mock 离线兜底：五档 → 本地三档（中级并入初级、精通并入高级）；联调走后端五档。
          const mockLevel = ({ 入门: '入门', 初级: '初级', 中级: '初级', 高级: '高级', 精通: '高级' } as const)[
            difficulty as '入门' | '初级' | '中级' | '高级' | '精通'
          ] ?? '初级'
          const md = USE_REAL_API
            ? (await getLecture(kpId, difficulty)).markdown
            : mock?.lectureByLevel?.[mockLevel] ?? ''
          if (alive) setLecture(md)
        } else if (kind === 'mindmap') {
          const md = USE_REAL_API
            ? lectureOutline((await getLecture(kpId, difficulty)).markdown)
            : mock?.mindmap ?? ''
          if (alive) setMindmap(md)
        } else if (kind === 'diagram') {
          const chart = USE_REAL_API ? (await getDiagram(kpId)).mermaid : mock?.diagram ?? ''
          if (alive) setDiagram(chart)
        }
      } catch (e) {
        console.error('[resource-library] 预览内容加载失败', e)
      } finally {
        if (alive) setLoading(false)
      }
    }
    void load()
    return () => {
      alive = false
    }
    // 记录不变；difficulty 由记录派生
  }, [kind, kpId, difficulty])

  /* 展示名 / 内容源：文档资源用文档标题 + 重建内容，内置资源沿用原有 kpId 取数。 */
  const displayName = isDoc ? docName : kpName
  const lectureMd = isDoc ? ((docData?.markdown as string) ?? '') : lecture
  const mindmapMd = isDoc ? ((docData?.markdown as string) ?? '') : mindmap
  const diagramChart = isDoc ? ((docData?.mermaid as string) ?? '') : diagram
  const busy = isDoc ? docLoading : loading

  /* 讲义导出（导出当前已渲染内容，不重新生成；文件名复用 sanitizeFilename，带正确扩展名） */
  const exportBaseName = record.difficulty
    ? `讲义-${displayName}-${record.difficulty}`
    : `讲义-${displayName}`
  const handleExportMarkdown = () => {
    if (lectureMd) exportLectureMarkdown(lectureMd, exportBaseName)
  }
  const handleExportPdf = () => {
    const body = lectureRef.current?.querySelector('.markdown-body') as HTMLElement | null
    if (!body) return
    const meta = `${displayName}${record.difficulty ? ` · 难度：${record.difficulty}` : ''} · 导出于 ${new Date().toLocaleString('zh-CN')}`
    const ok = exportLectureToPdf(body, exportBaseName, meta)
    if (!ok) window.alert('浏览器拦截了打印窗口，请允许本站弹出窗口后重试导出 PDF。')
  }

  const docSources = docData?.sources as SourceRef[] | undefined

  const renderBody = () => {
    // 文档资源内容加载失败 → 明确提示（不再回落到代码/内置内容）
    if (isDoc && docErr) {
      return <div className="resource-loading">该文档资源内容加载失败，请稍后重试。</div>
    }
    if (kind === 'lecture') {
      return (
        <>
          <div className="lecture-export">
            <span className="lecture-export__label">导出讲义：</span>
            <button
              type="button"
              className="lecture-export__btn"
              onClick={handleExportMarkdown}
              disabled={!lectureMd}
              title="下载讲义 Markdown 原文（.md）"
            >
              <span aria-hidden="true">⬇</span> Markdown
            </button>
            <button
              type="button"
              className="lecture-export__btn lecture-export__btn--pdf"
              onClick={handleExportPdf}
              disabled={!lectureMd}
              title="导出为 PDF（经浏览器打印 → 另存为 PDF）"
            >
              <span aria-hidden="true">🖨</span> PDF
            </button>
          </div>
          <div className="lecture-body" ref={lectureRef}>
            {lectureMd ? (
              <MarkdownRenderer content={lectureMd} />
            ) : (
              <div className="resource-loading">「{displayName}」的讲义内容加载中…</div>
            )}
          </div>
          {isDoc && <SourceTrace sources={docSources} />}
        </>
      )
    }
    if (kind === 'video') {
      return (
        <Suspense fallback={<Loading />}>
          <div className="resource-modal-hint">AI 生成的讲解视频（Remotion 渲染）+ 同步旁白，可播放/下载 mp4：</div>
          {isDoc ? (
            docLoading ? (
              <div className="resource-loading">「{displayName}」的讲解视频生成中…</div>
            ) : docData ? (
              <VideoLecture
                title={docData.title as string}
                scenes={docData.scenes as never}
                difficulty={(docData.difficulty as string) || difficulty}
              />
            ) : null
          ) : (
            <VideoLecture kpId={kpId} difficulty={difficulty} />
          )}
          {isDoc && <SourceTrace sources={docSources} />}
        </Suspense>
      )
    }
    if (kind === 'mindmap') {
      return (
        <Suspense fallback={<Loading />}>
          <div className="resource-modal-hint">结构化的知识脉络图，可缩放/拖拽，支持下载 SVG / PNG：</div>
          {busy ? (
            <div className="resource-loading">「{displayName}」的思维导图加载中…</div>
          ) : mindmapMd ? (
            <MindMap markdown={mindmapMd} downloadName={`思维导图-${displayName}`} />
          ) : (
            <div className="resource-loading">该思维导图暂无可用内容。</div>
          )}
        </Suspense>
      )
    }
    if (kind === 'diagram') {
      return (
        <Suspense fallback={<Loading />}>
          <div className="resource-modal-hint">「{displayName}」知识脉络图（可缩放/拖拽），支持下载 SVG / PNG：</div>
          {busy ? (
            <div className="resource-loading">「{displayName}」的知识图解加载中…</div>
          ) : diagramChart ? (
            <MermaidDiagram chart={diagramChart} downloadName={`图解-${displayName}`} />
          ) : (
            <div className="resource-loading">该图解暂无可用内容。</div>
          )}
          {isDoc && <SourceTrace sources={docSources} />}
        </Suspense>
      )
    }
    if (kind === 'quiz') {
      return (
        <>
          <div className="resource-modal-hint">基于文档生成的练习题，作答后即时判分与解析：</div>
          {docLoading ? (
            <div className="resource-loading">「{displayName}」的练习题加载中…</div>
          ) : docData && (docData.questions as unknown[])?.length ? (
            <QuizRenderer questions={docData.questions as never} />
          ) : (
            <div className="resource-loading">该练习题暂无可用内容。</div>
          )}
          {isDoc && <SourceTrace sources={docSources} />}
        </>
      )
    }
    if (kind === 'flashcard') {
      return (
        <>
          <div className="resource-modal-hint">正反翻卡速记，点击卡片翻面，← → 翻页浏览整套，可下载卡片集：</div>
          {docLoading ? (
            <div className="resource-loading">「{displayName}」的记忆闪卡加载中…</div>
          ) : docData && (docData.cards as unknown[])?.length ? (
            <FlashcardDeck
              cards={docData.cards as never}
              title={displayName}
              downloadName={`闪卡-${displayName}`}
            />
          ) : (
            <div className="resource-loading">该闪卡暂无可用内容。</div>
          )}
          {isDoc && <SourceTrace sources={docSources} />}
        </>
      )
    }
    // code（仅内置课程）：按记录的 kpId 取对应知识点 demo，与学习资源页同源
    return (
      <Suspense fallback={<Loading />}>
        <div className="resource-modal-hint">{kpCodeDemo(kpId).hint}</div>
        <CodeSandbox js={kpCodeDemo(kpId).js} baseName={`代码-${displayName}`} />
      </Suspense>
    )
  }

  return (
    <motion.div
      className="rescard-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={record.title}
    >
      <motion.div className="rescard-detail" onClick={(e) => e.stopPropagation()}>
        <motion.div
          className="rescard-detail__head rescard-detail__head--plain"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          style={{ background: KIND_THEME[kind] }}
        />
        <h3 className="rescard-detail__title">
          {KIND_LABEL[kind]} · {displayName}
          {record.difficulty ? ` · ${record.difficulty}` : ''}
        </h3>
        <button type="button" className="rescard-detail__close" onClick={onClose} autoFocus aria-label="关闭">
          ×
        </button>

        <motion.div
          className="rescard-detail__body"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.24 }}
        >
          {/* 文档资源跳「文档学习」页无实际内容（无选中文档、无上下文），故仅内置课程资源保留跳转；
             文档资源在本预览框内已可完整查看 + 下载 + 重新生成 + 编辑，无需跳转。 */}
          {!isDoc && (
            <div className="reslib-preview__toolbar">
              <button type="button" className="reslib-preview__jump" onClick={() => onOpenInLearning(record)}>
                在学习页打开 →
              </button>
            </div>
          )}
          {renderBody()}
        </motion.div>
      </motion.div>
    </motion.div>
  )
}
