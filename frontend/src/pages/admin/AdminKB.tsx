import { useCallback, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import PageHeader from '../../components/PageHeader'
import { USE_REAL_API } from '../../services/api'
import {
  listKbDocuments,
  uploadKbDocuments,
  deleteKbDocument,
  kbSearchTest,
  type KbDocument,
  type KbDocStatus,
  type KbSearchResult,
} from '../../services/admin'
import './admin.css'

/* 与 Dashboard 一致的入场 stagger */
const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
}
const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0 },
}

/** 入库状态徽章 → 复用全局 agent-status 视觉（接口文档 14.1 状态机） */
const STATUS_BADGE: Record<KbDocStatus, { label: string; cls: string; dot: string }> = {
  pending: { label: '待入库', cls: 'agent-status--idle', dot: 'status-dot--idle' },
  indexing: { label: '入库中', cls: 'agent-status--running', dot: 'status-dot--running' },
  indexed: { label: '已入库', cls: 'agent-status--success', dot: 'status-dot--success' },
  failed: { label: '失败', cls: 'agent-status--error', dot: 'status-dot--idle' },
}

const ACCEPT_EXT = ['.pdf', '.md', '.txt']

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function AdminKB() {
  /* ===== 文档列表 ===== */
  const [docs, setDocs] = useState<KbDocument[]>([])
  const [total, setTotal] = useState(0)
  const [listError, setListError] = useState<string | null>(null)

  /* ===== 上传 ===== */
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  /* ===== 删除确认 + 反馈 ===== */
  const [confirmDoc, setConfirmDoc] = useState<KbDocument | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  /* ===== 检索测试 ===== */
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<KbSearchResult | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const page = await listKbDocuments({ pageSize: 100 })
      setDocs(page.items)
      setTotal(page.total)
      setListError(null)
    } catch (err) {
      setListError(err instanceof Error ? err.message : '获取文档列表失败')
    }
  }, [])

  useEffect(() => {
    if (USE_REAL_API) void refresh()
  }, [refresh])

  /* 存在 pending/indexing 文档时每 2s 轮询，直至全部进入终态 */
  const hasActive = docs.some((d) => d.status === 'pending' || d.status === 'indexing')
  useEffect(() => {
    if (!hasActive) return
    const timer = setInterval(() => void refresh(), 2000)
    return () => clearInterval(timer)
  }, [hasActive, refresh])

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 3200)
  }

  const handleFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList).filter((f) =>
      ACCEPT_EXT.some((ext) => f.name.toLowerCase().endsWith(ext))
    )
    if (!files.length) {
      setUploadError(`仅支持 ${ACCEPT_EXT.join(' / ')} 文件`)
      return
    }
    setUploading(true)
    setUploadError(null)
    try {
      await uploadKbDocuments(files)
      await refresh() // 上传后立即刷新，pending 文档进入轮询
      showToast(`已提交 ${files.length} 份文档，切片向量化进行中…`)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDelete = async () => {
    if (!confirmDoc || deleting) return
    setDeleting(true)
    try {
      const res = await deleteKbDocument(confirmDoc.id)
      showToast(`已删除「${confirmDoc.title}」· 同步移除 ${res.removedChunks} 个向量切片`)
      setConfirmDoc(null)
      await refresh()
      /* 检索结果中该文档的命中同步消失 */
      setSearchResult((prev) =>
        prev ? { ...prev, results: prev.results.filter((r) => r.documentId !== res.id) } : prev
      )
    } catch (err) {
      showToast(err instanceof Error ? err.message : '删除失败')
      setConfirmDoc(null)
    } finally {
      setDeleting(false)
    }
  }

  const handleSearch = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const q = query.trim()
    if (!q || searching) return
    setSearching(true)
    setSearchError(null)
    try {
      setSearchResult(await kbSearchTest(q))
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : '检索失败')
      setSearchResult(null)
    } finally {
      setSearching(false)
    }
  }

  const totalChunks = docs.reduce((sum, d) => sum + d.chunks, 0)

  return (
    <motion.div className="akb" variants={containerVariants} initial="hidden" animate="visible">
      <PageHeader
        title="知识库管理"
        highlight="知识库"
        subtitle="文档入库 · 切片向量化 · RAG 检索质量测试"
        badges={[
          { label: '文档', value: total },
          { label: '切片', value: totalChunks, tone: 'accent' },
        ]}
      />

      {!USE_REAL_API && (
        <motion.div className="akb__offline glass-card" variants={itemVariants}>
          当前为前端 Mock 演示模式（未设 <code>VITE_USE_REAL_API=true</code>），知识库管理需连接后端使用。
        </motion.div>
      )}

      {/* ===== 上传区 ===== */}
      <motion.section
        className={`akb__upload glass-card ${dragOver ? 'akb__upload--over' : ''}`}
        variants={itemVariants}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          void handleFiles(e.dataTransfer.files)
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_EXT.join(',')}
          multiple
          hidden
          onChange={(e) => e.target.files && void handleFiles(e.target.files)}
        />
        <div className="akb__upload-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <path d="M17 8l-5-5-5 5" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
        </div>
        <div className="akb__upload-text">
          <strong>{uploading ? '正在上传…' : '拖拽文档到此处，或点击选择'}</strong>
          <span>支持 pdf / md / txt · 上传后自动切片向量化入库（异步任务）</span>
        </div>
        {uploadError && <p className="akb__upload-error">{uploadError}</p>}
      </motion.section>

      {/* ===== 文档列表 ===== */}
      <motion.section className="akb__list" variants={itemVariants}>
        <h3 className="akb__section-title">
          文档列表
          {hasActive && <span className="akb__polling">· 入库状态轮询中</span>}
        </h3>
        {listError && <p className="akb__error">{listError}</p>}
        {!listError && docs.length === 0 && (
          <p className="akb__empty">知识库为空，先上传一篇文档试试</p>
        )}
        <div className="akb__docs">
          <AnimatePresence initial={false}>
            {docs.map((doc) => {
              const badge = STATUS_BADGE[doc.status] ?? STATUS_BADGE.pending
              return (
                <motion.article
                  key={doc.id}
                  className="akb__doc glass-card"
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.96 }}
                  transition={{ duration: 0.25 }}
                >
                  <div className="akb__doc-main">
                    <h4 className="akb__doc-title">{doc.title}</h4>
                    <p className="akb__doc-meta">
                      <span className="akb__doc-file">{doc.filename}</span>
                      <span>{formatSize(doc.size)}</span>
                      {doc.category && <span className="akb__doc-cat">{doc.category}</span>}
                      <span>{formatTime(doc.uploadedAt)}</span>
                    </p>
                  </div>
                  <div className="akb__doc-side">
                    <span className={`agent-status ${badge.cls}`}>
                      <span className={`status-dot ${badge.dot}`} />
                      {badge.label}
                    </span>
                    <span className="akb__doc-chunks">{doc.chunks} 切片</span>
                    <button
                      className="akb__doc-delete"
                      title="删除文档及其向量切片"
                      onClick={() => setConfirmDoc(doc)}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 6h18" />
                        <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                      </svg>
                    </button>
                  </div>
                </motion.article>
              )
            })}
          </AnimatePresence>
        </div>
      </motion.section>

      {/* ===== 检索测试面板（复用 SourceTrace 视觉语言）===== */}
      <motion.section className="akb__search glass-card" variants={itemVariants}>
        <div className="akb__search-head">
          <span className="akb__search-shield">🛡️</span>
          <span className="akb__search-title">检索测试 · BM25 + 向量 RRF → bge-reranker 重排</span>
          {searchResult && (
            <span className={`akb__search-mode ${searchResult.rerankerUsed ? '' : 'akb__search-mode--degraded'}`}>
              {searchResult.rerankerUsed ? '已重排 bge-reranker' : '降级 · 仅 RRF'}
            </span>
          )}
        </div>

        <form className="akb__search-form" onSubmit={handleSearch}>
          <input
            className="akb__search-input"
            type="text"
            placeholder="输入查询语句，验证知识库召回质量，如：反向传播的梯度如何计算"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn btn--primary" type="submit" disabled={searching || !query.trim()}>
            {searching ? '检索中…' : '检索'}
          </button>
        </form>

        {searchError && <p className="akb__error">{searchError}</p>}
        {searchResult && searchResult.results.length === 0 && (
          <p className="akb__empty">未命中任何切片</p>
        )}

        <AnimatePresence initial={false}>
          {searchResult && searchResult.results.length > 0 && (
            <motion.ul
              className="akb__hits"
              initial="hidden"
              animate="visible"
              variants={{ visible: { transition: { staggerChildren: 0.06 } } }}
            >
              {searchResult.results.map((hit) => (
                <motion.li
                  key={hit.chunkId}
                  className="akb__hit"
                  variants={{ hidden: { opacity: 0, y: 10 }, visible: { opacity: 1, y: 0 } }}
                  exit={{ opacity: 0 }}
                  layout
                >
                  <div className="akb__hit-head">
                    <span className="akb__hit-doc">{hit.documentTitle}</span>
                    <span className="akb__hit-chunk">{hit.chunkId}</span>
                    <span className="akb__hit-score">
                      <span className="akb__hit-bar">
                        <span className="akb__hit-fill" style={{ width: `${Math.round(hit.score * 100)}%` }} />
                      </span>
                      {hit.score.toFixed(3)}
                    </span>
                  </div>
                  <p className="akb__hit-content">{hit.content}</p>
                  <div className="akb__hit-foot">
                    <span className="akb__hit-sub">vector {hit.vectorScore.toFixed(3)}</span>
                    {hit.bm25Score != null && <span className="akb__hit-sub">bm25 {hit.bm25Score.toFixed(2)}</span>}
                    <span className="akb__hit-loc">📍 {hit.sourceLocation}</span>
                  </div>
                </motion.li>
              ))}
            </motion.ul>
          )}
        </AnimatePresence>
      </motion.section>

      {/* ===== 删除确认弹层 ===== */}
      <AnimatePresence>
        {confirmDoc && (
          <motion.div
            className="akb__modal-mask"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => !deleting && setConfirmDoc(null)}
          >
            <motion.div
              className="akb__modal glass-card"
              initial={{ opacity: 0, scale: 0.94, y: 12 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
              onClick={(e) => e.stopPropagation()}
            >
              <h4>删除文档？</h4>
              <p>
                将删除「{confirmDoc.title}」及其全部 <strong>{confirmDoc.chunks}</strong> 个向量切片，
                删除后检索不再命中该文档，且不可恢复。
              </p>
              <div className="akb__modal-actions">
                <button className="btn btn--ghost" onClick={() => setConfirmDoc(null)} disabled={deleting}>
                  取消
                </button>
                <button className="btn btn--primary akb__modal-danger" onClick={() => void handleDelete()} disabled={deleting}>
                  {deleting ? '删除中…' : '确认删除'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ===== 操作反馈 toast ===== */}
      <AnimatePresence>
        {toast && (
          <motion.div
            className="akb__toast glass-card"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
