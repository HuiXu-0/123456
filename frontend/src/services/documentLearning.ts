/**
 * 文档学习·数据获取层（接口文档第 20 章：上传→独立向量集合→基于文档的生成 + 问答）。
 *
 * - 联调（USE_REAL_API）：走真实后端 /document/* 系列接口，生成内容严格溯源自用户上传文档；
 *   各生成接口由后端旁路以 source="document" 自动埋点到「我的资源库」。
 * - mock：全程本地合成（内存文档表 + 从文档正文派生的假产物），无任何后端 / Key 也能跑通
 *   「上传 → 选中 → 生成六类 / 问答 → 展示 / 下载」全链路（与项目 Mock-first 纪律一致）。
 *
 * 多文档统一生成（新增）：生成接口可传 documentIds（多篇合并检索范围统一生成）；未传则回退单篇。
 * 与 services/resource.ts（内置课程知识点资源）互不影响：这里只处理「用户自带文档」。
 */
import { apiGet, apiPost, apiPostForm, apiRequest, submitAsyncTask, pollTask, USE_REAL_API } from './api'
import type { LectureScene } from '../remotion/LectureVideo'
import type { QuizQuestion } from '../components/QuizRenderer'
import type { SourceRef } from '../components/SourceTrace'

/* ---------------- 类型（严格对齐接口文档 20.x 字段名） ---------------- */

export type DocFileType = 'pdf' | 'md' | 'txt' | 'docx'
export type DocStatus = 'pending' | 'indexing' | 'indexed' | 'failed'

/** 上传文档实体（20.1 document）。 */
export interface DocumentItem {
  id: string
  title: string
  filename: string
  fileType: DocFileType
  size: number
  status: DocStatus
  chunks: number
  error: string | null
  uploadedAt: string
}

export interface UploadResult {
  taskId: string
  document: DocumentItem
}

/** 后端 sources（confidence 为 0-1）→ SourceTrace 需要的 0-100 百分比。 */
interface RawSource {
  title: string
  type: string
  confidence: number
}
const SRC_TYPES: SourceRef['type'][] = ['教材', '论文', '文档', '课程']
export function toSourceRefs(raw?: RawSource[]): SourceRef[] {
  if (!raw?.length) return []
  return raw.map((s) => ({
    title: s.title,
    type: (SRC_TYPES.includes(s.type as SourceRef['type']) ? s.type : '文档') as SourceRef['type'],
    confidence: s.confidence <= 1 ? Math.round(s.confidence * 100) : Math.round(s.confidence),
  }))
}

export interface LectureResult {
  markdown: string
  difficulty: string
  sources: SourceRef[]
  hallucinationRate: number
}
export interface VideoResult {
  title: string
  difficulty: string
  scenes: LectureScene[]
  sources: SourceRef[]
}
export interface DiagramResult {
  mermaid: string
  sources: SourceRef[]
}
export interface MindmapResult {
  markdown: string
}
export interface QuizResult {
  questions: QuizQuestion[]
  sources: SourceRef[]
}
export interface FlashcardCard {
  front: string
  back: string
}
export interface FlashcardResult {
  cards: FlashcardCard[]
  sources: SourceRef[]
}
/** 文档概览（NotebookLM 式速读）：是什么 / 讲了什么 / 结构概况 / 关键点。 */
export interface OverviewResult {
  summary: string
  about: string
  structure: string
  keyPoints: string[]
  sources: SourceRef[]
}

/* ================================================================== *
 *  mock：内存文档表 + 从正文派生的确定性假产物（无后端时全链路可跑）
 * ================================================================== */

const mockDocs: DocumentItem[] = []
/** docId → 解析出的正文（用于让生成内容「看得出来自该文档」）。 */
const mockContent: Record<string, string> = {}

let mockSeq = 0
const mockId = () => `udoc_mock${(++mockSeq).toString(36)}${Date.now().toString(36).slice(-4)}`

function extToType(name: string): DocFileType {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (ext === 'pdf') return 'pdf'
  if (ext === 'md' || ext === 'markdown') return 'md'
  if (ext === 'docx' || ext === 'doc') return 'docx'
  return 'txt'
}

/** 从正文切出若干「要点句」（供派生讲义/闪卡/测试，体现内容取自文档）。 */
function keySentences(text: string, n: number): string[] {
  const parts = text
    .replace(/\s+/g, ' ')
    .split(/(?<=[。！？.!?；;\n])/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 6)
  if (parts.length >= n) return parts.slice(0, n)
  // 正文不足（如 PDF 无法本地解析）时用占位，保证结构完整
  const pad = Array.from({ length: n - parts.length }, (_, i) => `文档要点 ${parts.length + i + 1}`)
  return [...parts, ...pad]
}

/* ---- 多文档 mock 合并（体现「一次生成的合并检索范围」，来源覆盖多篇） ---- */

/** docId 列表 → 内存中的文档对象（保序、去空）。 */
function pickMockDocs(ids: string[]): DocumentItem[] {
  const out: DocumentItem[] = []
  const seen = new Set<string>()
  for (const id of ids) {
    if (!id || seen.has(id)) continue
    seen.add(id)
    const d = mockDocs.find((x) => x.id === id)
    if (d) out.push(d)
  }
  return out
}

/** 多篇统一标题：单篇取原标题，多篇标「首篇 等 N 篇文档」。空数组防御性回退。 */
function mergedTitle(docs: DocumentItem[]): string {
  if (!docs.length) return '文档'
  return docs.length === 1 ? docs[0].title : `${docs[0].title} 等 ${docs.length} 篇文档`
}

/** 跨多篇文档轮流抽取要点句（每句仍取自各自文档，体现来自多篇）。 */
function mergedSentences(docs: DocumentItem[], n: number): { text: string; docTitle: string }[] {
  const per = docs.map((d) => ({
    doc: d,
    sents: keySentences(mockContent[d.id] ?? '', Math.ceil(n / docs.length) + 2),
  }))
  const out: { text: string; docTitle: string }[] = []
  let i = 0
  while (out.length < n) {
    let added = false
    for (const p of per) {
      if (p.sents[i]) {
        out.push({ text: p.sents[i], docTitle: p.doc.title })
        added = true
        if (out.length >= n) break
      }
    }
    if (!added) break
    i++
  }
  return out.length ? out : [{ text: `文档要点 1`, docTitle: docs[0]?.title ?? '文档' }]
}

/** 多篇来源：每篇给出 1-2 条来源，标题带各自文档名（可看出来自哪几篇）。 */
function mergedSources(docs: DocumentItem[]): SourceRef[] {
  if (docs.length === 1) return mockSources(docs[0])
  return docs.flatMap((d, di) => [
    { title: `${d.title} · 段落 1`, type: '文档' as const, confidence: 93 - di * 3 },
    { title: `${d.title} · 段落 3`, type: '文档' as const, confidence: 85 - di * 3 },
  ])
}

const mockSources = (doc: DocumentItem): SourceRef[] => [
  { title: `${doc.title} · 段落 1`, type: '文档', confidence: 94 },
  { title: `${doc.title} · 段落 3`, type: '文档', confidence: 88 },
  { title: `${doc.title} · 段落 6`, type: '文档', confidence: 82 },
]

function buildMockLecture(docs: DocumentItem[], difficulty: string): LectureResult {
  const title = mergedTitle(docs)
  const sents = mergedSentences(docs, 6).map((s) => s.text)
  const srcNote =
    docs.length > 1
      ? `> 本讲义由 AI 基于你选中的 ${docs.length} 篇文档合并生成，内容严格取自这些文档（难度：${difficulty}）。`
      : `> 本讲义由 AI 基于你上传的《${docs[0].filename}》生成，内容严格取自文档（难度：${difficulty}）。`
  const md = [
    `# ${title}`,
    ``,
    srcNote,
    ``,
    `## 一、核心概览`,
    ``,
    sents[0],
    ``,
    `## 二、要点精讲`,
    ``,
    `1. ${sents[1]}`,
    `2. ${sents[2]}`,
    `3. ${sents[3]}`,
    ``,
    `## 三、延伸理解`,
    ``,
    sents[4],
    ``,
    `## 四、小结`,
    ``,
    `- ${sents[5]}`,
    `- 建议结合原文档对照复习，并完成配套练习题 / 闪卡巩固。`,
    ``,
  ].join('\n')
  return { markdown: md, difficulty, sources: mergedSources(docs), hallucinationRate: 0.12 }
}

function buildMockVideo(docs: DocumentItem[], difficulty: string): VideoResult {
  const title = mergedTitle(docs)
  const s = mergedSentences(docs, 6).map((x) => x.text)
  const scenes: LectureScene[] = [
    { title: `导入 · ${title}`, points: [s[0].slice(0, 28), '本视频取材自你选中的文档'], narration: `欢迎学习《${title}》。${s[0]}` },
    { title: '核心要点', points: [s[1].slice(0, 24), s[2].slice(0, 24)], narration: `${s[1]} ${s[2]}` },
    { title: '深入理解', points: [s[3].slice(0, 24), s[4].slice(0, 24)], narration: `${s[3]} ${s[4]}` },
    { title: '总结回顾', points: [s[5].slice(0, 24), '完成练习巩固'], narration: `本节小结：${s[5]}` },
  ]
  return { title, difficulty, scenes, sources: mergedSources(docs) }
}

function buildMockDiagram(docs: DocumentItem[]): DiagramResult {
  const title = mergedTitle(docs)
  const s = mergedSentences(docs, 4).map((x) => x.text.slice(0, 12).replace(/[[\]{}()"]/g, ''))
  const mermaid = [
    'flowchart TD',
    `  A["${title.slice(0, 14)}"] --> B["${s[0]}"]`,
    `  A --> C["${s[1]}"]`,
    `  B --> D["${s[2]}"]`,
    `  C --> E["${s[3]}"]`,
    `  D --> F["综合应用"]`,
    `  E --> F`,
  ].join('\n')
  return { mermaid, sources: mergedSources(docs) }
}

function buildMockOverview(doc: DocumentItem): OverviewResult {
  const s = keySentences(mockContent[doc.id] ?? '', 6)
  const topic = s[0].slice(0, 24).replace(/[，,。.；;：:\s]+$/, '')
  return {
    summary: `《${doc.title}》是一篇围绕「${topic}」展开的资料。`,
    about: s.slice(0, 2).join('').slice(0, 180),
    structure: `全文自「${s[0].slice(0, 14)}」切入，逐步展开到「${s[s.length - 1].slice(0, 14)}」，共梳理约 ${s.length} 个要点。`,
    keyPoints: s.slice(0, 5),
    sources: mockSources(doc),
  }
}

function buildMockMindmap(docs: DocumentItem[]): MindmapResult {
  if (docs.length > 1) {
    const lines = [`# ${mergedTitle(docs)}`]
    for (const d of docs) {
      lines.push(`## ${d.title.slice(0, 40)}`)
      for (const sent of keySentences(mockContent[d.id] ?? '', 4)) lines.push(`### ${sent.slice(0, 30)}`)
    }
    return { markdown: lines.join('\n') }
  }
  const doc = docs[0]
  const s = keySentences(mockContent[doc.id] ?? '', 6).map((t) => t.slice(0, 18))
  const md = [
    `# ${doc.title}`,
    `## 核心概览`,
    `### ${s[0]}`,
    `## 要点精讲`,
    `### ${s[1]}`,
    `### ${s[2]}`,
    `### ${s[3]}`,
    `## 延伸与小结`,
    `### ${s[4]}`,
    `### ${s[5]}`,
  ].join('\n')
  return { markdown: md }
}

function buildMockQuiz(docs: DocumentItem[], count: number): QuizResult {
  const title = mergedTitle(docs)
  const s = mergedSentences(docs, count + 2).map((x) => x.text)
  const questions: QuizQuestion[] = Array.from({ length: count }, (_, i) => ({
    question_id: `docq_${i + 1}`,
    question_type: i % 3 === 2 ? 'boolean' : 'single',
    question_text:
      i % 3 === 2
        ? `判断：根据《${title}》，“${s[i].slice(0, 22)}…”这一说法成立。`
        : `根据《${title}》，下列关于“${s[i].slice(0, 18)}”的说法，哪一项与文档一致？`,
    options:
      i % 3 === 2
        ? [
            { option_id: 'a', option_text: '正确' },
            { option_id: 'b', option_text: '错误' },
          ]
        : [
            { option_id: 'a', option_text: s[i].slice(0, 26) || '与文档一致的表述' },
            { option_id: 'b', option_text: '与文档无关的干扰项 ①' },
            { option_id: 'c', option_text: '与文档相反的表述' },
            { option_id: 'd', option_text: '与文档无关的干扰项 ②' },
          ],
    correct_answer: 'a',
    explanation: `依据文档原文：${s[i]}`,
  }))
  return { questions, sources: mergedSources(docs) }
}

function buildMockFlashcards(docs: DocumentItem[], count: number): FlashcardResult {
  const title = mergedTitle(docs)
  const s = mergedSentences(docs, count)
  const cards: FlashcardCard[] = s.slice(0, count).map((sent, i) => ({
    front: `关于《${title}》的要点 ${i + 1}：文档是怎么讲的？`,
    back: sent.text,
  }))
  return { cards, sources: mergedSources(docs) }
}

/**
 * 和文档对话·mock 兜底答案（严格基于选中文档正文合成，带 [n] 行内溯源）。
 * 供 services/documentChat.ts 在无后端 / SSE 失败时逐字流式吐出，全链路可跑。
 */
export function mockDocAnswer(
  docIds: string[],
  message: string
): { answer: string; sources: SourceRef[] } {
  const docs = pickMockDocs(docIds)
  if (!docs.length) {
    return { answer: '请先在左侧选择至少一篇文档，我才能基于它来回答。', sources: [] }
  }
  const title = mergedTitle(docs)
  const q = (message || '').trim().replace(/\s+/g, ' ')
  const focus = q.length > 40 ? `${q.slice(0, 40)}…` : q || '这个问题'
  const picked = mergedSentences(docs, 3)
  const hasText = picked.some((p) => !/^文档要点 \d+$/.test(p.text))
  if (!hasText) {
    return {
      answer: `关于「${focus}」，当前所选文档《${title}》在本地演示模式下未解析出可引用的正文内容（PDF/DOCX 需联调真实后端解析）。联调后我会严格基于文档原文作答并逐条标注出处。`,
      sources: mergedSources(docs),
    }
  }
  const lead = `根据你选中的《${title}》，就「${focus}」，文档中相关的内容如下：`
  const body = picked.map((p, i) => `${i + 1}. ${p.text}[${i + 1}]`).join('\n')
  const tail = '以上要点均出自文档原文（见下方「来源」标注）。如需更系统的梳理，可在右侧生成讲义或图解。'
  return { answer: `${lead}\n${body}\n${tail}`, sources: mergedSources(docs) }
}

/** mock：把上传文件读成文本（md/txt 可读；pdf/docx 本地读不了则给占位正文）。 */
function readFileText(file: File): Promise<string> {
  const t = extToType(file.name)
  if (t === 'pdf' || t === 'docx') {
    return Promise.resolve(
      `《${file.name}》为二进制文档（${t.toUpperCase()}），本地演示模式下不解析其正文。` +
        `联调真实后端后将完成解析、切块与向量化，并据此生成溯源可查的讲义、视频、图解、导图、练习题与闪卡。`
    )
  }
  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => resolve('')
    reader.readAsText(file)
  })
}

/* ================================================================== *
 *  对外 API（联调走后端，mock 走本地合成）
 * ================================================================== */

/** 20.1 上传文档（multipart，字段名 file）。mock 立即置 indexed。 */
export async function uploadDocument(file: File): Promise<UploadResult> {
  if (USE_REAL_API) {
    const form = new FormData()
    form.append('file', file)
    return apiPostForm<UploadResult>('/document/upload', form)
  }
  const text = await readFileText(file)
  const now = new Date().toISOString()
  const doc: DocumentItem = {
    id: mockId(),
    title: file.name.replace(/\.[^.]+$/, ''),
    filename: file.name,
    fileType: extToType(file.name),
    size: file.size,
    status: 'indexed',
    chunks: Math.max(1, Math.round((text.length || 200) / 180)),
    error: null,
    uploadedAt: now,
  }
  mockContent[doc.id] = text
  mockDocs.unshift(doc)
  return { taskId: `t_mock_${doc.id}`, document: doc }
}

/** 20.2 我的文档列表（按上传时间倒序）。 */
export async function listDocuments(): Promise<DocumentItem[]> {
  if (USE_REAL_API) {
    const d = await apiGet<{ items: DocumentItem[]; total: number }>('/document/list')
    return d.items ?? []
  }
  return [...mockDocs]
}

/** 20.3 文档详情（含 contentPreview）。用于上传后轮询状态直至 indexed。 */
export async function getDocument(docId: string): Promise<DocumentItem> {
  if (USE_REAL_API) {
    return apiGet<DocumentItem & { contentPreview?: string }>(`/document/${docId}`)
  }
  const d = mockDocs.find((x) => x.id === docId)
  if (!d) throw new Error('document not found')
  return d
}

/** 20.4 删除文档（连同其独立向量集合）。 */
export async function deleteDocument(docId: string): Promise<void> {
  if (USE_REAL_API) {
    await apiRequest(`/document/${docId}`, { method: 'DELETE' })
    return
  }
  const i = mockDocs.findIndex((x) => x.id === docId)
  if (i >= 0) mockDocs.splice(i, 1)
  delete mockContent[docId]
}

/** 上传后轮询文档状态直至 indexed / failed（联调用；mock 已 indexed 立即返回）。 */
export async function waitIndexed(
  docId: string,
  onTick?: (doc: DocumentItem) => void,
  tries = 20
): Promise<DocumentItem> {
  let doc = await getDocument(docId)
  onTick?.(doc)
  for (let i = 0; i < tries && (doc.status === 'pending' || doc.status === 'indexing'); i++) {
    await new Promise((r) => window.setTimeout(r, 1200))
    doc = await getDocument(docId)
    onTick?.(doc)
  }
  return doc
}

/* -------- 20.5 ~ 20.7 六类内容生成（均基于 documentId(s)，溯源自该/这些文档） -------- */

/** 归一：把（主 docId + 可选多篇 docIds）拼成请求体，向后兼容单篇。 */
function docScopeBody(docId: string, documentIds?: string[]): Record<string, unknown> {
  const ids = (documentIds ?? []).filter(Boolean)
  return ids.length > 1 ? { documentId: docId, documentIds: ids } : { documentId: docId }
}
function resolveMockScope(docId: string, documentIds?: string[]): DocumentItem[] {
  const ids = (documentIds?.length ? documentIds : [docId]).filter(Boolean)
  const docs = pickMockDocs(ids)
  if (docs.length) return docs
  // 资源库里的文档资源，其来源文档可能不在本次会话的内存 mockDocs 中（往次生成 / 刷新后清空）。
  // 合成占位文档，保证离线预览渲染占位内容而非在 buildMock*(docs[0]) 处崩溃（此前会白屏）。
  return (ids.length ? ids : [docId || 'udoc_unknown']).map((id) => ({
    id,
    title: id,
    filename: `${id}.md`,
    fileType: 'md' as const,
    size: 0,
    status: 'indexed' as const,
    chunks: 0,
    error: null,
    uploadedAt: new Date().toISOString(),
  }))
}

/** 文档概览（选中文档即自动生成展示）：内容取自该文档，联调走后端、离线走 mock。 */
export async function generateOverview(docId: string): Promise<OverviewResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{
      overview: { summary: string; about: string; structure: string; keyPoints: string[] }
      sources: RawSource[]
    }>('/document/generate/overview', { documentId: docId })
    const o = d.overview ?? { summary: '', about: '', structure: '', keyPoints: [] }
    return {
      summary: o.summary ?? '',
      about: o.about ?? '',
      structure: o.structure ?? '',
      keyPoints: Array.isArray(o.keyPoints) ? o.keyPoints : [],
      sources: toSourceRefs(d.sources),
    }
  }
  await mockDelay()
  return buildMockOverview(await getDocument(docId))
}

export async function generateLecture(
  docId: string,
  difficulty = '初级',
  documentIds?: string[],
  regenerate = false
): Promise<LectureResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{ markdown: string; difficulty: string; sources: RawSource[]; hallucinationRate: number }>(
      '/document/generate/lecture',
      { ...docScopeBody(docId, documentIds), difficulty, regenerate }
    )
    return {
      markdown: d.markdown,
      difficulty: d.difficulty ?? difficulty,
      sources: toSourceRefs(d.sources),
      hallucinationRate: d.hallucinationRate ?? 0,
    }
  }
  await mockDelay()
  return buildMockLecture(resolveMockScope(docId, documentIds), difficulty)
}

export async function generateVideo(
  docId: string,
  difficulty = '初级',
  documentIds?: string[],
  regenerate = false
): Promise<VideoResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{
      title: string
      difficulty: string
      scenes: { title: string; points: string[]; narration: string }[]
      sources: RawSource[]
    }>('/document/generate/video', { ...docScopeBody(docId, documentIds), difficulty, regenerate })
    return {
      title: d.title,
      difficulty: d.difficulty ?? difficulty,
      scenes: (d.scenes ?? []).map((s) => ({ title: s.title, points: s.points, narration: s.narration })),
      sources: toSourceRefs(d.sources),
    }
  }
  await mockDelay()
  return buildMockVideo(resolveMockScope(docId, documentIds), difficulty)
}

export async function generateDiagram(
  docId: string,
  documentIds?: string[],
  regenerate = false
): Promise<DiagramResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{ mermaid: string; sources: RawSource[] }>(
      '/document/generate/diagram',
      { ...docScopeBody(docId, documentIds), regenerate }
    )
    return { mermaid: d.mermaid, sources: toSourceRefs(d.sources) }
  }
  await mockDelay()
  return buildMockDiagram(resolveMockScope(docId, documentIds))
}

export async function generateMindmap(
  docId: string,
  documentIds?: string[],
  regenerate = false
): Promise<MindmapResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{ markdown: string }>('/document/generate/mindmap', {
      ...docScopeBody(docId, documentIds),
      regenerate,
    })
    return { markdown: d.markdown }
  }
  await mockDelay()
  return buildMockMindmap(resolveMockScope(docId, documentIds))
}

export async function generateQuiz(
  docId: string,
  count = 5,
  documentIds?: string[],
  regenerate = false
): Promise<QuizResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{ questions: QuizQuestion[]; sources: RawSource[] }>('/document/generate/quiz', {
      ...docScopeBody(docId, documentIds),
      count,
      regenerate,
    })
    return { questions: d.questions ?? [], sources: toSourceRefs(d.sources) }
  }
  await mockDelay()
  return buildMockQuiz(resolveMockScope(docId, documentIds), count)
}

export async function generateFlashcards(
  docId: string,
  count = 8,
  documentIds?: string[],
  regenerate = false
): Promise<FlashcardResult> {
  if (USE_REAL_API) {
    const d = await apiPost<{ cards: FlashcardCard[]; sources: RawSource[] }>('/document/generate/flashcards', {
      ...docScopeBody(docId, documentIds),
      count,
      regenerate,
    })
    return { cards: d.cards ?? [], sources: toSourceRefs(d.sources) }
  }
  await mockDelay()
  return buildMockFlashcards(resolveMockScope(docId, documentIds), count)
}

/** mock 生成拟真延时（体现「AI 生成中」loading 态）。 */
function mockDelay() {
  return new Promise((r) => window.setTimeout(r, 900))
}

/* ---------------- 展示辅助 ---------------- */

export const DOC_FILE_ACCEPT = '.pdf,.txt,.md,.markdown,.docx'
export const DOC_MAX_MB = 20

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/* ==================== 异步生成任务提交 + 轮询 ==================== */

/** 提交异步生成任务，返回 taskId（前端不阻塞，后续轮询获取结果）。 */
export async function submitGenerateLecture(
  docId: string,
  difficulty = '初级',
  documentIds?: string[],
  regenerate = false
): Promise<string> {
  if (!USE_REAL_API) {
    // Mock 模式：直接返回假 taskId，前端走 mock 轮询
    return 'mock_task_' + Date.now()
  }
  return submitAsyncTask('/document/generate/lecture?async_mode=true', {
    ...docScopeBody(docId, documentIds),
    difficulty,
    regenerate,
  })
}

export async function submitGenerateVideo(
  docId: string,
  difficulty = '初级',
  documentIds?: string[],
  regenerate = false
): Promise<string> {
  if (!USE_REAL_API) return 'mock_task_' + Date.now()
  return submitAsyncTask('/document/generate/video?async_mode=true', {
    ...docScopeBody(docId, documentIds),
    difficulty,
    regenerate,
  })
}

export async function submitGenerateDiagram(
  docId: string,
  documentIds?: string[],
  regenerate = false
): Promise<string> {
  if (!USE_REAL_API) return 'mock_task_' + Date.now()
  return submitAsyncTask('/document/generate/diagram?async_mode=true', {
    ...docScopeBody(docId, documentIds),
    regenerate,
  })
}

export async function submitGenerateMindmap(
  docId: string,
  documentIds?: string[],
  regenerate = false
): Promise<string> {
  if (!USE_REAL_API) return 'mock_task_' + Date.now()
  return submitAsyncTask('/document/generate/mindmap?async_mode=true', {
    ...docScopeBody(docId, documentIds),
    regenerate,
  })
}

export async function submitGenerateQuiz(
  docId: string,
  count = 5,
  documentIds?: string[],
  regenerate = false
): Promise<string> {
  if (!USE_REAL_API) return 'mock_task_' + Date.now()
  return submitAsyncTask('/document/generate/quiz?async_mode=true', {
    ...docScopeBody(docId, documentIds),
    count,
    regenerate,
  })
}

export async function submitGenerateFlashcards(
  docId: string,
  count = 8,
  documentIds?: string[],
  regenerate = false
): Promise<string> {
  if (!USE_REAL_API) return 'mock_task_' + Date.now()
  return submitAsyncTask('/document/generate/flashcards?async_mode=true', {
    ...docScopeBody(docId, documentIds),
    count,
    regenerate,
  })
}

/** 轮询生成任务结果（通用），返回最终产物数据。 */
export async function pollGenerateResult<T>(
  taskId: string,
  opts?: { onProgress?: (pct: number | null, status: string) => void; signal?: AbortSignal }
): Promise<T> {
  // Mock 模式：模拟延时后返回空结果
  if (!USE_REAL_API) {
    await mockDelay()
    return {} as T
  }
  const res = await pollTask<T>(taskId, opts)
  if (res.status === 'failed') {
    throw new Error(res.error?.message || '生成任务失败')
  }
  return res.result as T
}
