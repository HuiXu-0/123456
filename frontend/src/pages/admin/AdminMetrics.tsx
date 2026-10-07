import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import PageHeader from '../../components/PageHeader'
import CountUp from '../../components/CountUp'
import { USE_REAL_API } from '../../services/api'
import { getAdminMetrics, type AdminMetrics } from '../../services/admin'
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

/** 三 gauge 定义（接口文档 14.6：三比率 0-1；达标口径见各 target） */
interface GaugeDef {
  key: 'hallucinationRate' | 'adaptationRate' | 'coverageRate'
  label: string
  targetText: string
  /** 是否达标（B8 量化目标：幻觉率 <0.05、适配率 ≥0.85、覆盖率 ≥0.90） */
  isOk: (rate: number) => boolean
  desc: string
}

const GAUGES: GaugeDef[] = [
  {
    key: 'hallucinationRate',
    label: '幻觉率',
    targetText: '目标 < 5%',
    isOk: (r) => r < 0.05,
    desc: '逐句接地校验：未接地句数 / 总句数（15.3 口径）',
  },
  {
    key: 'adaptationRate',
    label: '适配率',
    targetText: '目标 ≥ 85%',
    isOk: (r) => r >= 0.85,
    desc: '难度 / 画像匹配达标占比',
  },
  {
    key: 'coverageRate',
    label: '覆盖率',
    targetText: '目标 ≥ 90%',
    isOk: (r) => r >= 0.9,
    desc: '已建资源知识点 / 知识点全集',
  },
]

/* 半环 gauge 几何：viewBox 120x68，r=50，半圆弧长 = πr ≈ 157.08 */
const GAUGE_ARC = Math.PI * 50

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function AdminMetrics() {
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setMetrics(await getAdminMetrics())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取指标失败')
    }
  }, [])

  useEffect(() => {
    if (USE_REAL_API) void load()
  }, [load])

  return (
    <motion.div className="akb" variants={containerVariants} initial="hidden" animate="visible">
      <PageHeader
        title="指标看板"
        highlight="指标"
        subtitle="幻觉率 · 适配率 · 覆盖率三仪表 · 比率为 B8 接入真实计算前的占位口径"
      />

      {!USE_REAL_API && (
        <motion.div className="akb__offline glass-card" variants={itemVariants}>
          当前为前端 Mock 演示模式（未设 <code>VITE_USE_REAL_API=true</code>），指标看板需连接后端使用。
        </motion.div>
      )}

      {error && <p className="akb__error">{error}</p>}

      {/* ===== 三 gauge ===== */}
      <motion.div className="amx__gauges" variants={itemVariants}>
        {GAUGES.map((g) => {
          const rate = metrics ? metrics[g.key] : 0
          const pct = rate * 100
          const ok = metrics ? g.isOk(rate) : null
          return (
            <article key={g.key} className="amx__gauge glass-card">
              <header className="amx__gauge-head">
                <h3>{g.label}</h3>
                {ok != null && (
                  <span className={`agent-status ${ok ? 'agent-status--success' : 'agent-status--error'}`}>
                    <span className={`status-dot ${ok ? 'status-dot--success' : 'status-dot--idle'}`} />
                    {ok ? '达标' : '未达标'}
                  </span>
                )}
              </header>

              <div className="amx__gauge-arc">
                <svg viewBox="0 0 120 68" aria-hidden="true">
                  {/* 轨道 */}
                  <path
                    d="M 10 60 A 50 50 0 0 1 110 60"
                    fill="none"
                    className="amx__arc-track"
                    strokeWidth="9"
                    strokeLinecap="round"
                  />
                  {/* 数值弧：strokeDasharray = 占比弧长 + 余量 */}
                  <motion.path
                    d="M 10 60 A 50 50 0 0 1 110 60"
                    fill="none"
                    className={`amx__arc-value ${ok === false ? 'amx__arc-value--warn' : ''}`}
                    strokeWidth="9"
                    strokeLinecap="round"
                    initial={{ strokeDasharray: `0 ${GAUGE_ARC}` }}
                    animate={{ strokeDasharray: `${(pct / 100) * GAUGE_ARC} ${GAUGE_ARC}` }}
                    transition={{ duration: 1.4, ease: 'easeOut' }}
                  />
                </svg>
                <div className="amx__gauge-value">
                  {/* CountUp + mono 数字（复用全局 GSAP 滚动组件） */}
                  <CountUp value={pct} decimals={1} suffix="%" className="amx__gauge-num" />
                  <span className="amx__gauge-target">{g.targetText}</span>
                </div>
              </div>

              <p className="amx__gauge-desc">{g.desc}</p>
            </article>
          )
        })}
      </motion.div>

      {/* ===== 计数统计行 ===== */}
      <motion.div className="amx__stats glass-card" variants={itemVariants}>
        {(
          [
            { key: 'kbDocuments', label: '知识库文档' },
            { key: 'kbChunks', label: '向量切片' },
            { key: 'generatedResources', label: '已生成资源' },
          ] as const
        ).map((s) => (
          <div key={s.key} className="amx__stat">
            <CountUp value={metrics ? metrics[s.key] : 0} className="amx__stat-num" />
            <span className="amx__stat-label">{s.label}</span>
          </div>
        ))}
        <span className="amx__stats-updated">统计于 {formatTime(metrics?.updatedAt ?? null)}</span>
      </motion.div>
    </motion.div>
  )
}
