import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { PageType } from '../App'
import PageHeader from '../components/PageHeader'
import MarkdownRenderer from '../components/MarkdownRenderer'
import QuizRenderer from '../components/QuizRenderer'
import SourceTrace from '../components/SourceTrace'
import FlashcardDeck from '../components/FlashcardDeck'
import DocumentChat from '../components/DocumentChat'
import ResourceTypeIcon, { type ResourceIconKind } from '../components/ResourceTypeIcon'
import { exportLectureMarkdown, exportLectureToPdf } from '../utils/lectureExport'
/* 复用学习资源页的共享样式（讲义导出条 / 难度切换 / loading / 各渲染组件的导出工具条等，
   均为 class 前缀样式、无全局副作用），保证复用的渲染/下载组件在本页样式一致。 */
import '../pages/LearningResource.css'
import {
  DOC_FILE_ACCEPT,
  DOC_MAX_MB,
  deleteDocument,
  formatBytes,
  generateOverview,
  listDocuments,
  uploadDocument,
  submitGenerateLecture,
  submitGenerateVideo,
  submitGenerateDiagram,
  submitGenerateMindmap,
  submitGenerateQuiz,
  submitGenerateFlashcards,
  pollGenerateResult,
  type DiagramResult,
  type DocumentItem,
  type FlashcardResult,
  type LectureResult,
  type MindmapResult,
  type QuizResult,
  type VideoResult,
} from '../services/documentLearning'
import { setTutorContext } from '../services/tutorBus'
import { useDocLearningStore } from '../store/documentLearning'
import { GEN_STAGES, VIDEO_STAGES, useStagedProgress } from '../components/genStatus'
import InlineErrorBoundary, { lazySafe } from '../components/InlineErrorBoundary'
import './DocumentLearning.css'

/* lazySafe + 元素级 InlineErrorBoundary：单个产物组件 chunk 拉取失败 / 渲染抛错只降级该块，
   不再沿树上抛到 PageErrorBoundary 打崩整个文档学习视图（实测崩溃堆栈即源于此路径）。 */
const MindMap = lazySafe(() => import('../components/MindMap'), '思维导图')
const MermaidDiagram = lazySafe(() => import('../components/MermaidDiagram'), '知识图解')
const VideoLecture = lazySafe(() => import('../components/VideoLecture'), '讲解视频')

const Loading = () => <div className="resource-loading">资源加载中…</div>

type Kind = 'lecture' | 'video' | 'diagram' | 'mindmap' | 'quiz' | 'flashcards'
const LEVELS = ['入门', '初级', '中级', '高级', '精通'] as const

/* 客户端阶段化进度（生成中 · N% · 阶段 + 进度条）已抽为共享原语 useStagedProgress
   （components/genStatus.tsx），与学习资源页共用同一套推进机制。 */

/** 预览弹层头部主题浅底（与「我的资源库」预览一致，深浅主题下均为柔和浅色，文字用 --ink）。 */
const KIND_HEAD: Record<Kind, string> = {
  lecture: '#e7f4ee',
  video: '#e8f0fb',
  diagram: '#fbf2e2',
  mindmap: '#efeafb',
  quiz: '#fbeaf1',
  flashcards: '#e5f5f1',
}

interface ArtifactBag {
  lecture?: LectureResult
  video?: VideoResult
  diagram?: DiagramResult
  mindmap?: MindmapResult
  quiz?: QuizResult
  flashcards?: FlashcardResult
}

const KIND_META: { id: Kind; label: string; desc: string }[] = [
  { id: 'lecture', label: '定制讲义', desc: '结构化讲义' },
  { id: 'video', label: '讲解视频', desc: '脚本 + 旁白' },
  { id: 'diagram', label: '知识图解', desc: '流程图' },
  { id: 'mindmap', label: '思维导图', desc: '脉络树' },
  { id: 'quiz', label: '练习题', desc: '分阶自测' },
  { id: 'flashcards', label: '记忆闪卡', desc: '翻卡速记' },
]

/* 文档学习形态 id（quiz/flashcards 平行链路）→ 统一资源类型图标 kind（flashcards→flashcard）。 */
const DOC_ICON_KIND: Record<Kind, ResourceIconKind> = {
  lecture: 'lecture',
  video: 'video',
  diagram: 'diagram',
  mindmap: 'mindmap',
  quiz: 'quiz',
  flashcards: 'flashcard',
}

const STATUS_LABEL: Record<DocumentItem['status'], string> = {
  pending: '待处理',
  indexing: '解析入库中',
  indexed: '就绪',
  failed: '失败',
}

export default function DocumentLearning({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  // ---- 全局状态（跨页面切换持久化） ----
  const docs = useDocLearningStore((s) => s.docs)
  const setDocs = useDocLearningStore((s) => s.setDocs)
  const focusedId = useDocLearningStore((s) => s.focusedId)
  const setFocusedId = useDocLearningStore((s) => s.setFocusedId)
  const checkedIds = useDocLearningStore((s) => s.checkedIds)
  const setCheckedIds = useDocLearningStore((s) => s.setCheckedIds)
  const bags = useDocLearningStore((s) => s.bags)
  const setBags = useDocLearningStore((s) => s.setBags)
  const genKinds = useDocLearningStore((s) => s.genKinds)
  const setGenKinds = useDocLearningStore((s) => s.setGenKinds)
  const genErr = useDocLearningStore((s) => s.genErr)
  const setGenErr = useDocLearningStore((s) => s.setGenErr)
  const difficulty = useDocLearningStore((s) => s.difficulty)
  const setDifficulty = useDocLearningStore((s) => s.setDifficulty)
  const overviews = useDocLearningStore((s) => s.overviews)
  const setOverviews = useDocLearningStore((s) => s.setOverviews)
  const overviewBusy = useDocLearningStore((s) => s.overviewBusy)
  const setOverviewBusy = useDocLearningStore((s) => s.setOverviewBusy)
  const docsLoaded = useDocLearningStore((s) => s.docsLoaded)
  const setDocsLoaded = useDocLearningStore((s) => s.setDocsLoaded)
  const setActiveTasks = useDocLearningStore((s) => s.setActiveTasks)
  const [uploading, setUploading] = useState(false)
  const [uploadErr, setUploadErr] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  /** scopeKey（勾选集合排序拼接）→ 已生成产物集合（切换范围即切换视图）。 */
  // bags/genKinds/genErr/difficulty 已提升到 useDocLearningStore
  const [activeKind, setActiveKind] = useState<Kind | null>(null)

  /** 各形态的客户端生成进度（阶段文案 + 百分比），驱动按钮进度条 / 弹层重生成进度。
      共享原语 useStagedProgress（与学习资源页同一套），卸载自动清理定时器。 */
  const { progress, setProgress, startProgress: startStaged, stopProgress } = useStagedProgress<Kind>()

  /** 大预览弹层：当前打开的形态（null = 关闭）。内容直接读已落库产物 bag，不重新生成。 */
  const [previewKind, setPreviewKind] = useState<Kind | null>(null)
  /** 预览标题重命名覆盖：key = `${scopeKey}::${kind}`；空则回落默认标题。 */
  const [titleOverrides, setTitleOverrides] = useState<Record<string, string>>({})
  const [renaming, setRenaming] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  /** 讲义简单文本编辑（改后写回产物 markdown，预览即时重渲染）。 */
  const [editingLecture, setEditingLecture] = useState(false)
  const [lectureDraft, setLectureDraft] = useState('')
  const previewLectureRef = useRef<HTMLDivElement>(null)

  const lectureRef = useRef<HTMLDivElement>(null)

  /* 挂载：拉取我的文档列表，默认聚焦 + 勾选第一篇。docsLoaded 控制首次 loading。 */
  useEffect(() => {
    // 已加载过则跳过 loading 态，直接用 store 缓存渲染
    if (!docsLoaded) setDocsLoaded(true)
    listDocuments()
      .then((items) => {
        console.log('[doclearn] listDocuments returned', items.length, 'docs')
        setDocs(items)
        if (items.length) {
          setFocusedId((cur) => cur ?? items[0].id)
          setCheckedIds((cur) => (cur.size ? cur : new Set([items[0].id])))
        }
        // 恢复未完成文档的后台轮询（切走再回来时接续）
        for (const d of items) {
          if (d.status === 'pending' || d.status === 'indexing') {
            useDocLearningStore.getState().startDocPolling(d.id)
          }
        }
      })
      .catch((e) => console.error('[doclearn] 加载文档列表失败', e))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* 挂载时恢复未完成的异步生成任务（切页面/刷新回来后续轮询）。 */
  useEffect(() => {
    const tasks = useDocLearningStore.getState().activeTasks
    for (const [taskKey, task] of Object.entries(tasks)) {
      if (task.status === 'succeeded' || task.status === 'failed') continue
      // 重新启动轮询
      const kind = task.kind
      const sk = task.scopeKey
      setGenKinds((prev) => new Set(prev).add(kind))
      startProgress(kind)
      pollGenerateResult(task.taskId, {
        onProgress: (pct) => {
          if (pct !== null) {
            setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
          }
        },
      })
        .then((data) => {
          setBags((prev) => ({ ...prev, [sk]: { ...prev[sk], [kind]: data } }))
          setActiveKind(kind)
          setPreviewKind(kind)
          stopProgress(kind, true)
        })
        .catch((e) => {
          console.error('[doclearn] 恢复任务失败', e)
          setGenErr(`任务恢复失败，请重新生成。`)
          stopProgress(kind, false)
        })
        .finally(() => {
          setGenKinds((prev) => {
            const next = new Set(prev)
            next.delete(kind)
            return next
          })
          setActiveTasks((prev) => {
            const next = { ...prev }
            delete next[taskKey]
            return next
          })
        })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ---------------- 派生：问答 / 生成范围 ---------------- */
  const checkedList = useMemo(() => docs.filter((d) => checkedIds.has(d.id)), [docs, checkedIds])
  const readyChecked = useMemo(() => checkedList.filter((d) => d.status === 'indexed'), [checkedList])
  const scopeIds = useMemo(() => readyChecked.map((d) => d.id), [readyChecked])
  const scopeKey = useMemo(() => [...scopeIds].sort().join(','), [scopeIds])
  const primaryId = useMemo(
    () => (focusedId && scopeIds.includes(focusedId) ? focusedId : scopeIds[0] ?? null),
    [focusedId, scopeIds]
  )
  const scopeTitle = useMemo(() => {
    if (readyChecked.length > 1) return `${readyChecked[0].title} 等 ${readyChecked.length} 篇文档`
    return readyChecked[0]?.title ?? '文档'
  }, [readyChecked])

  const focusedDoc = docs.find((d) => d.id === focusedId) ?? null
  const bag = scopeKey ? bags[scopeKey] ?? {} : {}

  /* 声明当前辅导上下文（聚焦文档）→ 供 App 顶层全局 dock / 选中即问该文档发起辅导。 */
  useEffect(() => {
    setTutorContext({
      kpId: focusedDoc ? `doc:${focusedDoc.id}` : 'doc-learning',
      kpName: focusedDoc?.title ?? '当前文档',
    })
  }, [focusedDoc])

  /* 切换生成范围：把视图切到该范围已生成的第一个产物（没有则回到操作引导）。 */
  useEffect(() => {
    const b = bags[scopeKey]
    const first = b ? (KIND_META.map((k) => k.id).find((k) => b[k]) ?? null) : null
    setActiveKind(first)
    setGenErr(null)
    // 切换生成范围：关闭旧范围的预览弹层与编辑态（产物已不再对应）
    setPreviewKind(null)
    setRenaming(false)
    setEditingLecture(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey])

  /* 聚焦文档就绪即自动生成「文档概览」（NotebookLM 式速读）；已生成 / 生成中不重复拉取。
     cancelled 标志防止快速切换文档时旧请求覆盖新文档的概览。 */
  useEffect(() => {
    if (!focusedId) return
    const doc = docs.find((d) => d.id === focusedId)
    if (!doc || doc.status !== 'indexed') return
    if (overviews[focusedId] || overviewBusy.has(focusedId)) return
    let cancelled = false
    setOverviewBusy((prev) => new Set(prev).add(focusedId))
    generateOverview(focusedId)
      .then((o) => {
        if (!cancelled) setOverviews((prev) => ({ ...prev, [focusedId]: o }))
      })
      .catch((e) => {
        if (!cancelled) console.error('[doclearn] 文档概览生成失败', e)
      })
      .finally(() => {
        if (!cancelled) {
          setOverviewBusy((prev) => {
            const next = new Set(prev)
            next.delete(focusedId)
            return next
          })
        }
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedId, docs])

  /* ---------------- 选择 ---------------- */

  /** 点击文档主体：聚焦 + 单选（范围收敛为该篇，行为与原单文档一致）。 */
  const selectSingle = (id: string) => {
    setFocusedId(id)
    setCheckedIds(new Set([id]))
  }
  /** 勾选/取消勾选：加入 / 移出合并范围（多选统一生成 / 问答）。 */
  const toggleCheck = (id: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setFocusedId((cur) => cur ?? id)
  }

  /* ---------------- 上传 ---------------- */

  const validate = (file: File): string | null => {
    const ok = DOC_FILE_ACCEPT.split(',').some((e) => file.name.toLowerCase().endsWith(e.trim()))
    if (!ok) return `不支持的文件类型：${file.name}（仅支持 PDF / TXT / MD / DOCX）`
    if (file.size > DOC_MAX_MB * 1024 * 1024) return `文件过大：${file.name}（上限 ${DOC_MAX_MB}MB）`
    return null
  }

  const handleFiles = async (files: FileList | File[]) => {
    const list = Array.from(files)
    if (!list.length) return
    setUploadErr(null)
    for (const file of list) {
      const err = validate(file)
      if (err) {
        setUploadErr(err)
        continue
      }
      setUploading(true)
      try {
        const { document } = await uploadDocument(file)
        setDocs((prev) => [document, ...prev.filter((d) => d.id !== document.id)])
        selectSingle(document.id)
        // 联调下解析/向量化异步：后台轮询状态直至 indexed（不依赖组件生命周期）
        if (document.status !== 'indexed' && document.status !== 'failed') {
          useDocLearningStore.getState().startDocPolling(document.id)
        }
      } catch (e) {
        console.error('[doclearn] 上传失败', e)
        setUploadErr(`上传失败：${file.name}`)
      } finally {
        setUploading(false)
      }
    }
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files?.length) void handleFiles(e.dataTransfer.files)
  }

  const handleDelete = async (docId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    const doc = docs.find((d) => d.id === docId)
    if (!window.confirm(`确定删除文档「${doc?.title ?? docId}」及其生成资料？`)) return
    try {
      await deleteDocument(docId)
    } catch (err) {
      console.error('[doclearn] 删除失败', err)
    }
    setDocs((prev) => prev.filter((d) => d.id !== docId))
    setCheckedIds((prev) => {
      const next = new Set(prev)
      next.delete(docId)
      return next
    })
    if (focusedId === docId) setFocusedId((cur) => (cur === docId ? null : cur))
  }

  /* ---------------- 生成进度（客户端阶段化推进，给出明确「进行中 · 到哪一步」反馈） ---------------- */

  /** 启动某形态的阶段化进度：视频（长任务）5 段更慢推进，其余 4 段（推进逻辑见共享原语）。 */
  const startProgress = (kind: Kind) =>
    startStaged(kind, kind === 'video' ? VIDEO_STAGES : GEN_STAGES, { slow: kind === 'video' })

  /* ---------------- 生成（基于勾选范围，主文档 primaryId + 合并 scopeIds） ---------------- */

  const runGenerate = async (kind: Kind, opts?: { diff?: string; regen?: boolean }) => {
    if (!primaryId || genKinds.has(kind)) return
    if (!scopeIds.length) {
      setGenErr('所选文档正在解析入库，请稍候再生成。')
      return
    }
    // 已生成且非强制重生成 → 直接切视图并打开大预览（读已落库产物，不重新生成）
    if (!opts?.regen && bags[scopeKey]?.[kind]) {
      setActiveKind(kind)
      setPreviewKind(kind)
      return
    }
    setGenErr(null)
    setGenKinds((prev) => new Set(prev).add(kind))
    startProgress(kind)
    const diff = opts?.diff ?? difficulty
    const ids = scopeIds
    const taskKey = `${scopeKey}:${kind}`

    try {
      // 1. 提交异步任务 → 拿到 taskId
      let taskId: string
      if (kind === 'lecture') taskId = await submitGenerateLecture(primaryId, diff, ids)
      else if (kind === 'video') taskId = await submitGenerateVideo(primaryId, diff, ids)
      else if (kind === 'diagram') taskId = await submitGenerateDiagram(primaryId, ids)
      else if (kind === 'mindmap') taskId = await submitGenerateMindmap(primaryId, ids)
      else if (kind === 'quiz') taskId = await submitGenerateQuiz(primaryId, 5, ids)
      else taskId = await submitGenerateFlashcards(primaryId, 8, ids)

      // 2. 记录活跃任务
      setActiveTasks((prev) => ({
        ...prev,
        [taskKey]: { taskId, status: 'running', kind, scopeKey },
      }))

      // 3. 轮询等待结果（带真实进度回调）
      let data: ArtifactBag[Kind]
      if (kind === 'lecture') {
        data = await pollGenerateResult<LectureResult>(taskId, {
          onProgress: (pct) => {
            if (pct !== null) {
              setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
            }
          },
        })
      } else if (kind === 'video') {
        data = await pollGenerateResult<VideoResult>(taskId, {
          onProgress: (pct) => {
            if (pct !== null) {
              setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
            }
          },
        })
      } else if (kind === 'diagram') {
        data = await pollGenerateResult<DiagramResult>(taskId, {
          onProgress: (pct) => {
            if (pct !== null) {
              setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
            }
          },
        })
      } else if (kind === 'mindmap') {
        data = await pollGenerateResult<MindmapResult>(taskId, {
          onProgress: (pct) => {
            if (pct !== null) {
              setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
            }
          },
        })
      } else if (kind === 'quiz') {
        data = await pollGenerateResult<QuizResult>(taskId, {
          onProgress: (pct) => {
            if (pct !== null) {
              setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
            }
          },
        })
      } else {
        data = await pollGenerateResult<FlashcardResult>(taskId, {
          onProgress: (pct) => {
            if (pct !== null) {
              setProgress((prev) => ({ ...prev, [kind]: { pct, stage: '生成中…' } }))
            }
          },
        })
      }

      // 4. 清除活跃任务 + 存储结果
      setActiveTasks((prev) => {
        const next = { ...prev }
        delete next[taskKey]
        return next
      })
      setBags((prev) => ({ ...prev, [scopeKey]: { ...prev[scopeKey], [kind]: data } }))
      setActiveKind(kind)
      setPreviewKind(kind)
      stopProgress(kind, true)
    } catch (e) {
      console.error('[doclearn] 生成失败', e)
      setActiveTasks((prev) => {
        const next = { ...prev }
        delete next[taskKey]
        return next
      })
      setGenErr(`「${KIND_META.find((k) => k.id === kind)?.label}」生成失败，请稍后重试。`)
      stopProgress(kind, false)
    } finally {
      setGenKinds((prev) => {
        const next = new Set(prev)
        next.delete(kind)
        return next
      })
    }
  }

  /** 点击形态卡：已生成 → 直接开大预览；未生成 → 触发生成（生成完自动开预览）。 */
  const openOrGenerate = (kind: Kind) => {
    if (genKinds.has(kind)) return
    if (bag[kind]) {
      setActiveKind(kind)
      setPreviewKind(kind)
    } else {
      void runGenerate(kind)
    }
  }

  /* 讲义难度切换 → 按新难度重生成讲义 */
  const changeLectureLevel = (lv: string) => {
    if (lv === (bag.lecture?.difficulty ?? difficulty) || genKinds.has('lecture')) return
    setDifficulty(lv)
    void runGenerate('lecture', { diff: lv, regen: true })
  }

  const exportLectureMd = () => {
    if (!bag.lecture) return
    exportLectureMarkdown(bag.lecture.markdown, `讲义-${scopeTitle}-${bag.lecture.difficulty}`)
  }
  const exportLecturePdf = () => {
    const body = lectureRef.current?.querySelector('.markdown-body') as HTMLElement | null
    if (!body) return
    const meta = `${scopeTitle} · 难度：${bag.lecture?.difficulty ?? difficulty} · 导出于 ${new Date().toLocaleString('zh-CN')}`
    const ok = exportLectureToPdf(body, `讲义-${scopeTitle}`, meta)
    if (!ok) window.alert('浏览器拦截了打印窗口，请允许本站弹出窗口后重试导出 PDF。')
  }

  /* ---------------- 大预览弹层：标题 / 重命名 / 讲义编辑 / 弹层内导出 ---------------- */

  const kindLabel = (k: Kind) => KIND_META.find((m) => m.id === k)?.label ?? ''
  const titleKeyFor = (k: Kind) => `${scopeKey}::${k}`
  const defaultTitleFor = (k: Kind) => `${kindLabel(k)} · ${scopeTitle}`
  const effTitleFor = (k: Kind) => titleOverrides[titleKeyFor(k)] || defaultTitleFor(k)
  /** 下载文件名基名：重命名过则用自定义标题，否则回落 scopeTitle（不带形态前缀，避免与前缀重复）。 */
  const dlBase = (k: Kind) => titleOverrides[titleKeyFor(k)] || scopeTitle

  const beginRename = () => {
    if (!previewKind) return
    setTitleDraft(effTitleFor(previewKind))
    setRenaming(true)
  }
  const commitRename = () => {
    if (!previewKind) return
    const v = titleDraft.trim()
    const key = titleKeyFor(previewKind)
    setTitleOverrides((prev) => {
      const next = { ...prev }
      if (!v || v === defaultTitleFor(previewKind)) delete next[key]
      else next[key] = v
      return next
    })
    setRenaming(false)
  }

  const beginEditLecture = () => {
    if (!bag.lecture) return
    setLectureDraft(bag.lecture.markdown)
    setEditingLecture(true)
  }
  const saveLecture = () => {
    setBags((prev) => {
      const b = prev[scopeKey]
      if (!b?.lecture) return prev
      return { ...prev, [scopeKey]: { ...b, lecture: { ...b.lecture, markdown: lectureDraft } } }
    })
    setEditingLecture(false)
  }

  /** 弹层内讲义导出：复用现有导出能力，文件名带（可能被重命名过的）标题。 */
  const exportPreviewMd = () => {
    if (bag.lecture) exportLectureMarkdown(bag.lecture.markdown, `讲义-${dlBase('lecture')}`)
  }
  const exportPreviewPdf = () => {
    const body = previewLectureRef.current?.querySelector('.markdown-body') as HTMLElement | null
    if (!body) return
    const meta = `${effTitleFor('lecture')} · 难度：${bag.lecture?.difficulty ?? difficulty} · 导出于 ${new Date().toLocaleString('zh-CN')}`
    const ok = exportLectureToPdf(body, `讲义-${dlBase('lecture')}`, meta)
    if (!ok) window.alert('浏览器拦截了打印窗口，请允许本站弹出窗口后重试导出 PDF。')
  }

  const closePreview = () => {
    setPreviewKind(null)
    setRenaming(false)
    setEditingLecture(false)
  }

  /* 预览弹层：Esc 关闭 + 锁背景滚动（与「我的资源库」预览一致体验）。 */
  useEffect(() => {
    if (!previewKind) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePreview()
    }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKind])

  /* ---------------- 渲染：产出区 ---------------- */

  const renderOutput = () => {
    if (!activeKind) return null
    switch (activeKind) {
      case 'lecture':
        return (
          <>
            <div className="level-switch">
              <span className="level-switch__label">难度自适应：</span>
              <div className="level-switch__seg">
                {LEVELS.map((lv) => (
                  <button
                    key={lv}
                    className={`level-switch__btn ${(bag.lecture?.difficulty ?? difficulty) === lv ? 'level-switch__btn--active' : ''}`}
                    onClick={() => changeLectureLevel(lv)}
                    disabled={genKinds.has('lecture')}
                  >
                    {lv}
                  </button>
                ))}
              </div>
              <span className="level-switch__hint">切换难度，AI 按该文档实时重生成讲义</span>
            </div>
            {bag.lecture && (
              <div className="doclearn-halluc" title="讲义内容与文档原文的偏离度，越低越可信">
                文档溯源 · 幻觉率 {Math.round(bag.lecture.hallucinationRate * 100)}%
              </div>
            )}
            <SourceTrace sources={bag.lecture?.sources} />
            <div className="lecture-export">
              <span className="lecture-export__label">导出讲义：</span>
              <button type="button" className="lecture-export__btn" onClick={exportLectureMd} disabled={!bag.lecture}>
                <span aria-hidden="true">⬇</span> Markdown
              </button>
              <button
                type="button"
                className="lecture-export__btn lecture-export__btn--pdf"
                onClick={exportLecturePdf}
                disabled={!bag.lecture}
              >
                <span aria-hidden="true">🖨</span> PDF
              </button>
            </div>
            <div className="lecture-body" ref={lectureRef}>
              {bag.lecture ? (
                <MarkdownRenderer content={bag.lecture.markdown} />
              ) : (
                <div className="resource-loading">正在基于文档生成讲义…</div>
              )}
            </div>
          </>
        )
      case 'video':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">基于文档生成的讲解视频（Remotion 实时渲染 + 同步旁白）：</div>
            {bag.video && (
              <VideoLecture title={bag.video.title} scenes={bag.video.scenes} difficulty={bag.video.difficulty} />
            )}
            <SourceTrace sources={bag.video?.sources} />
          </Suspense>
        )
      case 'diagram':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">文档知识脉络图解（可缩放 / 拖拽 / 导出 SVG · PNG）：</div>
            {bag.diagram && (
              <MermaidDiagram chart={bag.diagram.mermaid} downloadName={`图解-${scopeTitle}`} />
            )}
            <SourceTrace sources={bag.diagram?.sources} />
          </Suspense>
        )
      case 'mindmap':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">文档结构化思维导图（可缩放 / 拖拽 / 导出 SVG · PNG）：</div>
            {bag.mindmap && (
              <MindMap markdown={bag.mindmap.markdown} downloadName={`思维导图-${scopeTitle}`} />
            )}
          </Suspense>
        )
      case 'quiz':
        return (
          <>
            <div className="resource-modal-hint">基于文档生成的练习题，作答后即时判分与解析（通过线 70 分）：</div>
            {bag.quiz && bag.quiz.questions.length > 0 ? (
              <QuizRenderer
                questions={bag.quiz.questions}
                autoGrade
                passMark={70}
                onRestudy={() => void runGenerate('lecture')}
              />
            ) : (
              <div className="resource-loading">正在基于文档生成练习题…</div>
            )}
            <SourceTrace sources={bag.quiz?.sources} />
          </>
        )
      case 'flashcards':
        return (
          <>
            <div className="resource-modal-hint">正反翻卡速记，点击卡片翻面，← → 翻页浏览整套：</div>
            {bag.flashcards && (
              <FlashcardDeck
                cards={bag.flashcards.cards}
                title={scopeTitle}
                downloadName={`闪卡-${scopeTitle}`}
              />
            )}
            <SourceTrace sources={bag.flashcards?.sources} />
          </>
        )
    }
  }

  /* ---------------- 渲染：大预览弹层的内容体（读已落库产物 bag，不重新生成） ---------------- */
  const renderPreviewBody = () => {
    if (!previewKind) return null
    switch (previewKind) {
      case 'lecture':
        return (
          <>
            <div className="level-switch">
              <span className="level-switch__label">难度自适应：</span>
              <div className="level-switch__seg">
                {LEVELS.map((lv) => (
                  <button
                    key={lv}
                    className={`level-switch__btn ${(bag.lecture?.difficulty ?? difficulty) === lv ? 'level-switch__btn--active' : ''}`}
                    onClick={() => changeLectureLevel(lv)}
                    disabled={genKinds.has('lecture') || editingLecture}
                  >
                    {lv}
                  </button>
                ))}
              </div>
              <span className="level-switch__hint">切换难度，AI 按该文档实时重生成讲义</span>
            </div>
            {bag.lecture && (
              <div className="doclearn-halluc" title="讲义内容与文档原文的偏离度，越低越可信">
                文档溯源 · 幻觉率 {Math.round(bag.lecture.hallucinationRate * 100)}%
              </div>
            )}
            <SourceTrace sources={bag.lecture?.sources} />
            {editingLecture ? (
              <div className="docprev__edit">
                <textarea
                  className="docprev__editor"
                  value={lectureDraft}
                  onChange={(e) => setLectureDraft(e.target.value)}
                  spellCheck={false}
                  aria-label="编辑讲义 Markdown"
                />
                <div className="docprev__edit-actions">
                  <button type="button" className="docprev__btn docprev__btn--primary" onClick={saveLecture}>
                    保存
                  </button>
                  <button type="button" className="docprev__btn" onClick={() => setEditingLecture(false)}>
                    取消
                  </button>
                  <span className="docprev__edit-hint">支持 Markdown / KaTeX 公式 / mermaid 图块，保存后即时渲染。</span>
                </div>
              </div>
            ) : (
              <div className="lecture-body" ref={previewLectureRef}>
                {bag.lecture ? (
                  <MarkdownRenderer content={bag.lecture.markdown} />
                ) : (
                  <div className="resource-loading">正在基于文档生成讲义…</div>
                )}
              </div>
            )}
          </>
        )
      case 'video':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">基于文档生成的讲解视频（Remotion 实时渲染 + 同步旁白）：</div>
            {bag.video && (
              <VideoLecture title={bag.video.title} scenes={bag.video.scenes} difficulty={bag.video.difficulty} />
            )}
            <SourceTrace sources={bag.video?.sources} />
          </Suspense>
        )
      case 'diagram':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">文档知识脉络图解（可缩放 / 拖拽 / 导出 SVG · PNG）：</div>
            {bag.diagram && <MermaidDiagram chart={bag.diagram.mermaid} downloadName={`图解-${dlBase('diagram')}`} />}
            <SourceTrace sources={bag.diagram?.sources} />
          </Suspense>
        )
      case 'mindmap':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">文档结构化思维导图（可缩放 / 拖拽 / 导出 SVG · PNG）：</div>
            {bag.mindmap && <MindMap markdown={bag.mindmap.markdown} downloadName={`思维导图-${dlBase('mindmap')}`} />}
          </Suspense>
        )
      case 'quiz':
        return (
          <>
            <div className="resource-modal-hint">基于文档生成的练习题，作答后即时判分与解析（通过线 70 分）：</div>
            {bag.quiz && bag.quiz.questions.length > 0 ? (
              <QuizRenderer
                questions={bag.quiz.questions}
                autoGrade
                passMark={70}
                onRestudy={() => void runGenerate('lecture')}
              />
            ) : (
              <div className="resource-loading">正在基于文档生成练习题…</div>
            )}
            <SourceTrace sources={bag.quiz?.sources} />
          </>
        )
      case 'flashcards':
        return (
          <>
            <div className="resource-modal-hint">正反翻卡速记，点击卡片翻面，← → 翻页浏览整套：</div>
            {bag.flashcards && (
              <FlashcardDeck
                cards={bag.flashcards.cards}
                title={effTitleFor('flashcards')}
                downloadName={`闪卡-${dlBase('flashcards')}`}
              />
            )}
            <SourceTrace sources={bag.flashcards?.sources} />
          </>
        )
    }
  }

  const readyCount = docs.filter((d) => d.status === 'indexed').length

  /* 左栏「关于来源」概览块（聚焦文档的 AI 速读）。 */
  const renderOverview = () => {
    if (!focusedDoc || focusedDoc.status !== 'indexed') return null
    if (!overviews[focusedId!] && !overviewBusy.has(focusedId!)) return null
    const ov = overviews[focusedId!]
    return (
      <div className="doclearn-overview">
        <div className="doclearn-overview__head">
          <span className="doclearn-overview__icon">🧭</span>
          <span className="doclearn-overview__title">文档概览</span>
          <span className="doclearn-overview__tag">AI 速读 · 溯源自本文档</span>
          {overviewBusy.has(focusedId!) && !ov && <span className="doclearn-drop__spinner" />}
        </div>
        <div className="doclearn-overview__docname">{focusedDoc.title}</div>
        {ov ? (
          <>
            <p className="doclearn-overview__summary">{ov.summary}</p>
            {ov.about && <p className="doclearn-overview__about">{ov.about}</p>}
            {ov.structure && (
              <p className="doclearn-overview__structure">
                <b>核心结构概况：</b>
                {ov.structure}
              </p>
            )}
            {ov.keyPoints.length > 0 && (
              <div className="doclearn-overview__keys">
                <span className="doclearn-overview__keys-label">关键点</span>
                <ul>
                  {ov.keyPoints.map((k, i) => (
                    <li key={i}>{k}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          <p className="doclearn-overview__summary">正在通读文档、生成概览…</p>
        )}
      </div>
    )
  }

  return (
    <div className="doclearn">
      <PageHeader
        title="文档学习"
        highlight="文档"
        subtitle="上传你的资料，左栏管理来源与概览、中栏和文档即问即答、右栏一键生成六类学习资源 · 内容严格溯源自文档"
        crumb="文档学习"
        onBack={() => onNavigate?.('dashboard')}
        badges={[
          { label: '我的文档', value: docs.length },
          { label: '已就绪', value: readyCount, tone: 'safe' },
          { label: '本次范围', value: readyChecked.length, tone: 'accent' },
        ]}
      />

      <div className="doclearn__grid">
        {/* ============ 左栏 · 来源与概览 ============ */}
        <aside className="doclearn__col doclearn__sources">
          <div className="doclearn__panel-title">
            <span>来源与概览</span>
            <span className="doclearn__panel-count">{docs.length}</span>
          </div>

          {/* 上传拖放区 */}
          <div
            className={`doclearn-drop ${dragOver ? 'doclearn-drop--over' : ''} ${uploading ? 'doclearn-drop--busy' : ''}`}
            onClick={() => !uploading && fileInputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={DOC_FILE_ACCEPT}
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) void handleFiles(e.target.files)
                e.target.value = ''
              }}
            />
            {uploading ? (
              <>
                <span className="doclearn-drop__spinner" />
                <span className="doclearn-drop__text">上传解析中…</span>
              </>
            ) : (
              <>
                <span className="doclearn-drop__icon">⬆</span>
                <span className="doclearn-drop__text">
                  拖放文件到此，或<b>点击上传</b>（可多选）
                </span>
                <span className="doclearn-drop__hint">支持 PDF · TXT · Markdown · DOCX（≤ {DOC_MAX_MB}MB）</span>
              </>
            )}
          </div>
          {uploadErr && <div className="doclearn-drop__err">{uploadErr}</div>}

          {/* 文档列表（勾选 = 合并范围；点击标题 = 聚焦单选） */}
          {docs.length > 0 && (
            <div className="doclearn-doclist__hint">
              勾选可多选，基于多篇<b>统一生成 / 问答</b>；点击标题查看该文档概览
            </div>
          )}
          <div className="doclearn-doclist">
            {!docsLoaded ? (
              <div className="resource-loading">加载文档…</div>
            ) : docs.length === 0 ? (
              <div className="doclearn-doclist__empty">还没有文档，先上传一份开始学习吧。</div>
            ) : (
              docs.map((d) => {
                const checked = checkedIds.has(d.id)
                const focused = focusedId === d.id
                return (
                  <div
                    key={d.id}
                    className={`doclearn-docitem ${focused ? 'doclearn-docitem--active' : ''} ${checked ? 'doclearn-docitem--checked' : ''}`}
                  >
                    <input
                      type="checkbox"
                      className="doclearn-docitem__check"
                      checked={checked}
                      onChange={() => toggleCheck(d.id)}
                      aria-label={`勾选「${d.title}」加入生成/问答范围`}
                      title="加入本次生成 / 问答范围"
                    />
                    <button className="doclearn-docitem__main" onClick={() => selectSingle(d.id)}>
                      <span className={`doclearn-docitem__type doclearn-docitem__type--${d.fileType}`}>
                        {d.fileType.toUpperCase()}
                      </span>
                      <span className="doclearn-docitem__body">
                        <span className="doclearn-docitem__title">{d.title}</span>
                        <span className="doclearn-docitem__meta">
                          <span className={`doclearn-docitem__status doclearn-docitem__status--${d.status}`}>
                            {STATUS_LABEL[d.status]}
                          </span>
                          · {formatBytes(d.size)} · {d.chunks} 块
                        </span>
                      </span>
                    </button>
                    <span
                      className="doclearn-docitem__del"
                      onClick={(e) => handleDelete(d.id, e)}
                      role="button"
                      tabIndex={-1}
                      aria-label="删除文档"
                      title="删除"
                    >
                      ×
                    </span>
                  </div>
                )
              })
            )}
          </div>

          {/* 文档概览（移至左栏「关于来源」信息区） */}
          {renderOverview()}

          {/* 底部留白区：小图标空状态提示（纯展示） */}
          <div className="doclearn-sources__spare" aria-hidden="true">
            <span className="doclearn-sources__spare-icon">📄</span>
            <p>
              {docs.length
                ? '可继续上传资料；勾选多篇即可跨文档统一问答与生成'
                : '已上传的资料会显示在此。支持 PDF、文本、Markdown、Word'}
            </p>
          </div>
        </aside>

        {/* ============ 中栏 · 和文档问答（流式即问即答、溯源、历史保留） ============ */}
        <section className="doclearn__col doclearn__chat">
          <DocumentChat key={scopeKey || 'empty'} docIds={scopeIds} docTitles={readyChecked.map((d) => d.title)} />
        </section>

        {/* ============ 右栏 · 生成六类 + 产出 ============ */}
        <section className="doclearn__col doclearn__studio">
          <div className="doclearn__panel-title">
            <span>生成学习资源</span>
          </div>
          {!primaryId ? (
            <div className="doclearn-empty">
              <div className="doclearn-empty__art">📚</div>
              <h3 className="doclearn-empty__title">选中文档，一键生成资源</h3>
              <p className="doclearn-empty__desc">
                在左栏上传并勾选文档后，这里可基于所选文档
                <br />
                生成讲义、视频、图解、思维导图、练习题与闪卡，生成后进「我的资源库」可下载。
              </p>
              <button className="doclearn-empty__cta" onClick={() => fileInputRef.current?.click()}>
                ⬆ 上传文档
              </button>
            </div>
          ) : (
            <>
              {/* 生成操作区 */}
              <div className="doclearn-actions">
                <div className="doclearn-actions__head">
                  <span className="doclearn-actions__doc">
                    生成范围：<b>{scopeTitle}</b>
                    {readyChecked.length > 1 && <span className="doclearn-actions__multi">合并 {readyChecked.length} 篇</span>}
                  </span>
                </div>
                <div className="doclearn-actions__grid">
                  {KIND_META.map((k) => {
                    const done = !!bag[k.id]
                    const busy = genKinds.has(k.id)
                    const active = activeKind === k.id
                    const pr = progress[k.id]
                    const pct = Math.round(pr?.pct ?? 0)
                    return (
                      <button
                        key={k.id}
                        className={`doclearn-act doclearn-act--${k.id} ${active ? 'doclearn-act--active' : ''} ${done ? 'doclearn-act--done' : ''} ${busy ? 'doclearn-act--busy' : ''}`}
                        onClick={() => openOrGenerate(k.id)}
                        disabled={busy || !scopeIds.length}
                        title={done ? '点击在大预览中查看' : `基于所选文档生成${k.label}`}
                      >
                        <span className="doclearn-act__icon">
                          {busy ? <span className="doclearn-drop__spinner" /> : <ResourceTypeIcon kind={DOC_ICON_KIND[k.id]} size={30} />}
                        </span>
                        <span className="doclearn-act__label">{k.label}</span>
                        <span className="doclearn-act__desc">
                          {busy
                            ? `生成中 · ${pct}%${pr?.stage ? ` · ${pr.stage}` : ''}`
                            : done
                              ? '已生成 · 点击查看'
                              : k.desc}
                        </span>
                        {busy && (
                          <span className="doclearn-act__progress" aria-hidden="true">
                            <span className="doclearn-act__progress-fill" style={{ width: `${pct}%` }} />
                          </span>
                        )}
                        {done && !busy && (
                          <span
                            className="doclearn-act__regen"
                            role="button"
                            tabIndex={-1}
                            title="重新生成"
                            onClick={(e) => {
                              e.stopPropagation()
                              if (!genKinds.has(k.id)) void runGenerate(k.id, { regen: true })
                            }}
                          >
                            ↻
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
                {genErr && <div className="doclearn-drop__err">{genErr}</div>}
              </div>

              {/* 产出区 */}
              <div className="doclearn-output">
                <AnimatePresence mode="wait">
                  {activeKind ? (
                    <motion.div
                      key={activeKind}
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -8 }}
                      transition={{ duration: 0.25 }}
                    >
                      {/* 元素级兜底：单个产物渲染异常只降级该产物块，左/中栏与操作区照常 */}
                      <InlineErrorBoundary label={kindLabel(activeKind)}>{renderOutput()}</InlineErrorBoundary>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="out-empty"
                      className="doclearn-output__empty"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                    >
                      <span className="doclearn-output__empty-icon">✨</span>
                      <p>
                        选择上方任一形态，AI 会<b>基于「{scopeTitle}」</b>为你生成对应学习资料。
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </>
          )}
        </section>
      </div>

      {/* ============ 大预览弹层（读已落库产物；框内可下载 / 重新生成 / 重命名 · 编辑） ============ */}
      <AnimatePresence>
        {previewKind && (
          <motion.div
            className="rescard-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closePreview}
            role="dialog"
            aria-modal="true"
            aria-label={effTitleFor(previewKind)}
          >
            <motion.div
              className="rescard-detail rescard-detail--wide"
              onClick={(e) => e.stopPropagation()}
              initial={{ opacity: 0, y: 24, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 24, scale: 0.97 }}
              transition={{ type: 'spring', stiffness: 240, damping: 24 }}
            >
              <div
                className="rescard-detail__head rescard-detail__head--plain"
                style={{ background: KIND_HEAD[previewKind] }}
              />
              <button type="button" className="rescard-detail__close" onClick={closePreview} aria-label="关闭">
                ×
              </button>

              {/* 标题 + 重命名 */}
              <div className="docprev__title">
                <span className="docprev__kind-chip">{kindLabel(previewKind)}</span>
                {renaming ? (
                  <input
                    className="docprev__title-input"
                    value={titleDraft}
                    autoFocus
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename()
                      else if (e.key === 'Escape') setRenaming(false)
                    }}
                    aria-label="重命名标题"
                  />
                ) : (
                  <>
                    <span className="docprev__title-text">{effTitleFor(previewKind)}</span>
                    <button type="button" className="docprev__rename" title="重命名标题" onClick={beginRename}>
                      ✎
                    </button>
                  </>
                )}
              </div>

              <div className="rescard-detail__body">
                {/* 框内操作条：下载 / 重新生成 / 编辑 */}
                <div className="docprev__toolbar">
                  {previewKind === 'lecture' && !editingLecture && (
                    <>
                      <button type="button" className="docprev__btn" onClick={exportPreviewMd} disabled={!bag.lecture}>
                        <span aria-hidden="true">⬇</span> Markdown
                      </button>
                      <button type="button" className="docprev__btn" onClick={exportPreviewPdf} disabled={!bag.lecture}>
                        <span aria-hidden="true">🖨</span> PDF
                      </button>
                      <button type="button" className="docprev__btn" onClick={beginEditLecture} disabled={!bag.lecture}>
                        <span aria-hidden="true">✎</span> 编辑
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className="docprev__btn docprev__btn--primary"
                    onClick={() => void runGenerate(previewKind, { regen: true })}
                    disabled={genKinds.has(previewKind) || editingLecture}
                    title="基于文档重新生成并刷新预览"
                  >
                    <span aria-hidden="true">↻</span> {genKinds.has(previewKind) ? '重新生成中…' : '重新生成'}
                  </button>
                </div>

                {/* 重新生成进度条 */}
                {genKinds.has(previewKind) && progress[previewKind] && (
                  <div className="docprev__regen">
                    <span className="docprev__regen-bar" aria-hidden="true">
                      <span
                        className="docprev__regen-fill"
                        style={{ width: `${Math.round(progress[previewKind]!.pct)}%` }}
                      />
                    </span>
                    <span>
                      重新生成中 · {Math.round(progress[previewKind]!.pct)}% · {progress[previewKind]!.stage}
                    </span>
                  </div>
                )}

                {/* 元素级兜底：预览弹层内单个产物渲染异常只降级弹层内容，弹层与页面照常 */}
                <InlineErrorBoundary key={previewKind} label={kindLabel(previewKind)}>
                  {renderPreviewBody()}
                </InlineErrorBoundary>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
