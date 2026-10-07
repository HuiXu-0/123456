import { useEffect, useMemo, useState } from 'react'
import { getJobMarket, getHotJobs, timeAgo, HOT_JOBS, type HotJob, type JobMarket } from '../services/jobMarket'
import { USE_REAL_API } from '../services/api'
import './JobMarketPanel.css'

interface JobMarketPanelProps {
  /** 选中岗位（含其 radar 岗位要求）回传父级，用于雷达"岗位要求"线与 Gap */
  onSelect?: (job: JobMarket | null) => void
  /** 岗位数据离线状态回传父级（热门列表降级 或 快照降级 任一为真），供页级标签跟随 */
  onOffline?: (offline: boolean) => void
}

/** 目标岗位 · 实时市场画像（联网快照 + 缓存 + 降级）替代静态岗位 chip */
export default function JobMarketPanel({ onSelect, onOffline }: JobMarketPanelProps) {
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [data, setData] = useState<JobMarket | null>(null)
  const [offline, setOffline] = useState(false)
  const [loading, setLoading] = useState(false)

  /* 热门岗位列表：联调走 GET /job-market/hot 覆盖（降级语义见 getHotJobs——
     空列表/失败/2002 一律回预置 4 岗 + offline，选择器永不为空）；
     mock 保持内置 HOT_JOBS（初值即是，渲染零差异）*/
  const [hotJobs, setHotJobs] = useState<HotJob[]>(HOT_JOBS)
  const [hotOffline, setHotOffline] = useState(false)
  useEffect(() => {
    if (!USE_REAL_API) return
    let alive = true
    getHotJobs().then((r) => {
      if (!alive) return
      setHotJobs(r.jobs)
      setHotOffline(r.offline)
    })
    return () => {
      alive = false
    }
  }, [])

  /* 页级离线状态：热门列表降级 或 当前快照降级，任一为真即离线 */
  useEffect(() => {
    onOffline?.(hotOffline || offline)
  }, [hotOffline, offline, onOffline])

  const suggestions = useMemo(() => {
    const q = query.trim()
    if (!q) return hotJobs
    return hotJobs.filter((j) => j.name.includes(q))
  }, [query, hotJobs])

  useEffect(() => {
    if (!selectedId) return
    let alive = true
    setLoading(true)
    getJobMarket(selectedId)
      .then((res) => {
        if (!alive) return
        setData(res.data)
        setOffline(res.offline)
        onSelect?.(res.data)
      })
      .catch(() => {
        if (!alive) return
        setData(null)
        onSelect?.(null)
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [selectedId, onSelect])

  return (
    <div className="jobmkt">
      {/* 搜索 + 热门岗位 */}
      <div className="jobmkt__search">
        <svg className="jobmkt__search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          className="jobmkt__search-input"
          type="text"
          placeholder="搜索目标岗位，如 大模型应用工程师"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && suggestions[0]) setSelectedId(suggestions[0].id)
          }}
        />
      </div>

      <div className="jobmkt__hot">
        <span className="jobmkt__hot-label">热门岗位</span>
        {suggestions.map((j) => (
          <button
            key={j.id}
            className={`jobmkt__chip ${selectedId === j.id ? 'jobmkt__chip--active' : ''}`}
            onClick={() => setSelectedId(j.id)}
          >
            {j.name}
          </button>
        ))}
        {suggestions.length === 0 && <span className="jobmkt__empty">未找到匹配岗位</span>}
      </div>

      {/* 市场画像卡 */}
      {!selectedId && (
        <div className="jobmkt__placeholder">搜索或选择目标岗位，查看实时市场画像</div>
      )}

      {selectedId && loading && <div className="jobmkt__placeholder">正在获取市场画像…</div>}

      {selectedId && !loading && data && (
        <div className="jobmkt__card">
          <div className="jobmkt__card-head">
            <div className="jobmkt__title">
              <h3>{data.name}</h3>
              {offline ? (
                <span className="jobmkt__tag jobmkt__tag--offline">离线版本 · 预置库</span>
              ) : (
                <span className="jobmkt__tag jobmkt__tag--live">
                  <span className="jobmkt__live-dot" />实时市场数据
                </span>
              )}
            </div>
            <div className="jobmkt__meta">
              更新于 {timeAgo(data.fetchedAt)} · 来源：{data.source}
            </div>
          </div>

          {/* 关键指标 */}
          <div className="jobmkt__metrics">
            <div className="jobmkt__metric">
              <span className="jobmkt__metric-value">{data.salaryRange}</span>
              <span className="jobmkt__metric-label">薪资区间 · 中位 {data.salaryMedian}</span>
            </div>
            <div className="jobmkt__metric">
              <span className="jobmkt__metric-value">{data.heat}</span>
              <span className="jobmkt__metric-label">招聘热度 {data.heatPct}%</span>
            </div>
            <div className="jobmkt__metric">
              <span className="jobmkt__metric-value">{data.openings.toLocaleString()}</span>
              <span className="jobmkt__metric-label">在招职位数</span>
            </div>
          </div>

          {/* 主流技能要求（按 JD 出现频率）*/}
          <div className="jobmkt__skills">
            <span className="jobmkt__skills-title">当前主流技能要求 · 按 JD 出现频率</span>
            {data.skills.map((s) => (
              <div className="jobmkt__skill" key={s.name}>
                <span className="jobmkt__skill-name">{s.name}</span>
                <span className="jobmkt__skill-bar">
                  <span className="jobmkt__skill-fill" style={{ width: `${s.freqPct}%` }} />
                </span>
                <span className="jobmkt__skill-pct">{s.freqPct}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
