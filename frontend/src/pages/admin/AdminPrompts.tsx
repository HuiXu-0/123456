import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import PageHeader from '../../components/PageHeader'
import { USE_REAL_API } from '../../services/api'
import {
  getPromptTemplate,
  updatePromptTemplate,
  type AgentId,
  type PromptTemplate,
} from '../../services/admin'
import './admin.css'

/* 与 AdminKB 一致的入场 stagger */
const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
}
const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0 },
}

/** 三 Agent 页签（接口文档 14.5：agentId 固定 3 项，与 11.2 工作流对齐） */
const AGENT_TABS: { id: AgentId; label: string; desc: string }[] = [
  { id: 'diagnosis', label: '学情诊断', desc: '从画像与掌握度定位薄弱知识点' },
  { id: 'generation', label: '领域知识生成', desc: '依据 RAG 上下文生成难度适配讲义' },
  { id: 'critic', label: '内容审核校验', desc: '逐句接地校验，标记疑似幻觉' },
]

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function AdminPrompts() {
  const [activeId, setActiveId] = useState<AgentId>('diagnosis')
  /** 各 Agent 已加载的模板（含编辑后回写的新 version/updatedAt） */
  const [templates, setTemplates] = useState<Partial<Record<AgentId, PromptTemplate>>>({})
  /** 编辑区文本（按 Agent 暂存，切页签不丢编辑） */
  const [drafts, setDrafts] = useState<Partial<Record<AgentId, string>>>({})
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const current = templates[activeId]
  const draft = drafts[activeId] ?? current?.template ?? ''
  const dirty = current != null && draft !== current.template

  /** 编辑文本中缺失的必需占位符（与后端 1001 校验同口径，提前提示） */
  const missingVars = useMemo(
    () => (current ? current.variables.filter((v) => !draft.includes(`{${v}}`)) : []),
    [current, draft]
  )

  const load = useCallback(async (agentId: AgentId) => {
    setLoading(true)
    setError(null)
    try {
      const tpl = await getPromptTemplate(agentId)
      setTemplates((prev) => ({ ...prev, [agentId]: tpl }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取模板失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (USE_REAL_API && !templates[activeId]) void load(activeId)
    // eslint 不在依赖里加 templates：仅按页签首次加载
  }, [activeId, load]) // eslint-disable-line react-hooks/exhaustive-deps

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 3200)
  }

  const handleSave = async () => {
    if (!current || !dirty || saving || missingVars.length > 0) return
    setSaving(true)
    setError(null)
    try {
      const res = await updatePromptTemplate(activeId, draft)
      /* 保存成功：回写新版本号与更新时间，模板即热更新 */
      setTemplates((prev) => ({
        ...prev,
        [activeId]: { ...current, template: draft, version: res.version, updatedAt: res.updatedAt },
      }))
      setDrafts((prev) => ({ ...prev, [activeId]: draft }))
      showToast(`「${current.name}」已保存并热更新 · 版本 v${res.version}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <motion.div className="akb" variants={containerVariants} initial="hidden" animate="visible">
      <PageHeader
        title="Prompt 管理"
        highlight="Prompt"
        subtitle="三 Agent 模板编辑 · 保存即热更新，下次生成调用立即生效"
      />

      {!USE_REAL_API && (
        <motion.div className="akb__offline glass-card" variants={itemVariants}>
          当前为前端 Mock 演示模式（未设 <code>VITE_USE_REAL_API=true</code>），Prompt 管理需连接后端使用。
        </motion.div>
      )}

      {/* ===== Agent 页签 ===== */}
      <motion.div className="apr__tabs" variants={itemVariants}>
        {AGENT_TABS.map((tab) => (
          <button
            key={tab.id}
            className={`apr__tab glass-card ${activeId === tab.id ? 'apr__tab--active' : ''}`}
            onClick={() => setActiveId(tab.id)}
          >
            <span className="apr__tab-id">{tab.id}</span>
            <strong>{tab.label} Agent</strong>
            <span className="apr__tab-desc">{tab.desc}</span>
          </button>
        ))}
      </motion.div>

      {/* ===== 模板编辑区 ===== */}
      <motion.section className="apr__editor glass-card" variants={itemVariants}>
        {error && <p className="akb__error">{error}</p>}
        {loading && !current && <p className="akb__empty">模板加载中…</p>}
        {!USE_REAL_API && !current && !loading && (
          <p className="akb__empty">连接后端后展示「{AGENT_TABS.find((t) => t.id === activeId)?.label}」模板</p>
        )}

        {current && (
          <>
            <div className="apr__meta">
              <h3 className="apr__name">{current.name}</h3>
              <span className="apr__version">v{current.version}</span>
              <span className="apr__updated">更新于 {formatTime(current.updatedAt)}</span>
              {dirty && <span className="apr__dirty">未保存</span>}
            </div>

            <div className="apr__vars">
              <span className="apr__vars-label">必需占位符</span>
              {current.variables.map((v) => (
                <code
                  key={v}
                  className={`apr__var ${missingVars.includes(v) ? 'apr__var--missing' : ''}`}
                  title={missingVars.includes(v) ? '模板中缺失该占位符，保存将被拒绝' : '模板已包含'}
                >
                  {`{${v}}`}
                </code>
              ))}
            </div>

            <textarea
              className="apr__textarea"
              value={draft}
              spellCheck={false}
              rows={12}
              onChange={(e) => setDrafts((prev) => ({ ...prev, [activeId]: e.target.value }))}
            />

            <div className="apr__actions">
              {missingVars.length > 0 && (
                <span className="apr__warn">缺失占位符：{missingVars.map((v) => `{${v}}`).join('、')}</span>
              )}
              <button
                className="btn btn--ghost"
                disabled={!dirty || saving}
                onClick={() => setDrafts((prev) => ({ ...prev, [activeId]: current.template }))}
              >
                还原
              </button>
              <button
                className="btn btn--primary"
                disabled={!dirty || saving || missingVars.length > 0}
                onClick={() => void handleSave()}
              >
                {saving ? '保存中…' : '保存并热更新'}
              </button>
            </div>
          </>
        )}
      </motion.section>

      {/* ===== 操作反馈 toast（复用 AdminKB 形态）===== */}
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
