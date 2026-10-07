import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { motion, AnimatePresence } from 'framer-motion'
import { useChartTokens, withAlpha } from '../utils/chartTheme'
import PageHeader from '../components/PageHeader'
import { RevealGroup, RevealItem } from '../components/Reveal'
import { useMastery } from '../store/mastery'
import { USE_REAL_API } from '../services/api'
import { getKnowledgeSystem } from '../services/knowledgeSystem'
import {
  KNOWLEDGE_SYSTEM_SEED,
  BOARD_META,
  BOARD_BY_CODE,
  LEVEL_ORDER,
  LEVELS,
  deriveBoardSummaries,
  type KnowledgeSystemData,
  type KsPoint,
  type KsLevel,
} from '../data/knowledgeSystem'
import { setResourceNav } from '../services/resourceNav'
import type { PageType } from '../App'
import './KnowledgeGraph.css'

/* ── 纯色工具：HEX 混合，用于「层级 = 明度」着色（入门浅→进阶本色→前沿深） ── */
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}
function shadeLevel(color: string, level: KsLevel, dark: boolean): string {
  const [r, g, b] = hexToRgb(color)
  const toward = (tr: number, tg: number, tb: number, t: number) =>
    `rgb(${Math.round(r + (tr - r) * t)}, ${Math.round(g + (tg - g) * t)}, ${Math.round(b + (tb - b) * t)})`
  if (level === '入门') return toward(255, 255, 255, dark ? 0.28 : 0.46) // 浅
  if (level === '前沿') return dark ? toward(255, 255, 255, 0.14) : toward(20, 22, 18, 0.2) // 深/亮
  return color // 进阶：本色
}

/* 视图状态机 */
type View = 'boards' | 'board'

/* 布局节点（像素坐标，layout:'none'） */
interface LayoutNode {
  id: string
  x: number
  y: number
}

export default function KnowledgeGraph({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const t = useChartTokens()
  const masteryStatus = useMastery((s) => s.status)

  /* 数据源：真实模式请求 10.2；mock / 失败回退种子（78 点齐全，先修/层级完整）*/
  const [data, setData] = useState<KnowledgeSystemData>(KNOWLEDGE_SYSTEM_SEED)
  useEffect(() => {
    if (!USE_REAL_API) return
    let alive = true
    getKnowledgeSystem()
      .then((d) => alive && d?.points?.length && setData(d))
      .catch((e) => console.error('[knowledge-system] 加载体系失败，回退种子', e))
    return () => {
      alive = false
    }
  }, [masteryStatus])

  const points = data.points
  const pointMap = useMemo(() => new Map(points.map((p) => [p.id, p])), [points])
  const boardSummaries = useMemo(
    () => (data.boards.length ? deriveBoardSummaries(points) : []),
    [points, data.boards.length]
  )
  const coverageByBoard = useMemo(
    () => new Map((data.coverage ?? []).map((c) => [c.board, c])),
    [data.coverage]
  )

  /* 视图状态 */
  const [view, setView] = useState<View>('boards')
  const [activeBoard, setActiveBoard] = useState<string>('ML')
  const [detail, setDetail] = useState<KsPoint | null>(null)

  /* resize / 主题变化触发重排（layout:'none' 用容器像素坐标，需按尺寸重算）*/
  const [sizeTick, setSizeTick] = useState(0)

  const openBoard = (code: string) => {
    setActiveBoard(code)
    setView('board')
    setDetail(null)
  }
  const backToBoards = () => {
    setView('boards')
    setDetail(null)
  }
  /* 打开某点详情（跨板块先修点亦可跳转：切到其板块并选中）*/
  const openPoint = (id: string) => {
    const p = pointMap.get(id)
    if (!p) return
    setActiveBoard(p.category)
    setView('board')
    setDetail(p)
  }
  /* 去学习：核心点直达有序学习流；非核心目录点落资源总览（hub），
     内容由生成引擎按需生成（78 点全体系可学，resourceNav 已放行体系 kpId）。 */
  const goLearn = (p: KsPoint) => {
    setResourceNav(p.id, p.isCore ? 'flow' : undefined)
    onNavigate?.('learning-resource')
  }

  /* ── ECharts 渲染（视图/板块/数据/主题/尺寸变化时重建 option）── */
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (!chart.current) chart.current = echarts.init(el)
    const c = chart.current
    const W = el.clientWidth || 900
    const H = el.clientHeight || 520
    const dark = document.documentElement.getAttribute('data-theme') === 'dark'
    const passed = (id: string) => masteryStatus[id] === 'passed'

    const baseTooltip = {
      backgroundColor: withAlpha(t.surface, 0.96),
      borderColor: withAlpha(t.primary, 0.25),
      borderWidth: 1,
      textStyle: { color: t.ink, fontSize: 12 },
      extraCssText: 'border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.12);',
    }

    let option: echarts.EChartsOption

    if (view === 'boards') {
      /* ── 第一层：7 板块概览 ── */
      const padX = 96
      const xs = BOARD_META.map((b) => b.pos[0])
      const minX = Math.min(...xs)
      const maxX = Math.max(...xs)
      const yScale = Math.min(H * 0.3, 150)
      const nodes = BOARD_META.map((b) => {
        const sum = boardSummaries.find((s) => s.code === b.code)
        const total = sum?.total ?? 0
        const cov = coverageByBoard.get(b.code)
        const px = padX + ((b.pos[0] - minX) / (maxX - minX || 1)) * (W - 2 * padX)
        const py = H / 2 + b.pos[1] * yScale
        return {
          id: b.code,
          name: b.name,
          x: px,
          y: py,
          symbolSize: 58 + total * 2.6,
          itemStyle: {
            color: b.color,
            borderColor: withAlpha('#ffffff', dark ? 0.28 : 0.7),
            borderWidth: 2,
            shadowBlur: 20,
            shadowColor: withAlpha(b.color, 0.42),
          },
          label: {
            show: true,
            position: 'inside' as const,
            formatter: `{s|${b.short}}\n{n|${total}}`,
            rich: {
              s: { color: '#fff', fontSize: 17, fontWeight: 800, lineHeight: 20, align: 'center' as const },
              n: { color: withAlpha('#ffffff', 0.85), fontSize: 12, fontWeight: 600, align: 'center' as const },
            },
          },
          _tt: cov
            ? `<b>${b.name}</b><br/>知识点 ${total} · 层级 ${sum ? Object.entries(sum.levels).filter(([, v]) => v).map(([k, v]) => `${k}${v}`).join(' / ') : ''}<br/>覆盖 ${Math.round((cov.coveragePct || 0) * 100)}%（已测 ${cov.tested} · 推断 ${cov.inferred}）`
            : `<b>${b.name}</b><br/>知识点 ${total}<br/>点击展开板块内知识点`,
        }
      })
      /* 跨板块聚合依赖：prereq 所属板块 → 该点板块 */
      const agg = new Map<string, number>()
      points.forEach((p) => {
        p.prerequisites.forEach((pre) => {
          const src = pointMap.get(pre)?.category
          if (src && src !== p.category) {
            const key = `${src}>${p.category}`
            agg.set(key, (agg.get(key) ?? 0) + 1)
          }
        })
      })
      const links = [...agg.entries()].map(([key, count]) => {
        const [s, d] = key.split('>')
        return {
          source: s,
          target: d,
          lineStyle: {
            color: withAlpha(BOARD_BY_CODE[s]?.color ?? t.inkSoft, dark ? 0.5 : 0.42),
            width: 1 + Math.min(count, 6) * 0.6,
            curveness: 0.18,
          },
        }
      })

      option = {
        tooltip: { ...baseTooltip, formatter: (p: any) => (p.dataType === 'node' ? p.data._tt : '') },
        animationDuration: 700,
        animationEasingUpdate: 'quinticInOut',
        series: [
          {
            type: 'graph',
            layout: 'none',
            roam: true,
            draggable: true,
            data: nodes,
            links,
            edgeSymbol: ['none', 'arrow'],
            edgeSymbolSize: 7,
            emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
            lineStyle: { opacity: 0.85 },
          } as any,
        ],
      }

      c.setOption(option, true)
      c.off('click')
      c.on('click', (p: any) => {
        if (p.dataType === 'node') openBoard(p.data.id)
      })
    } else {
      /* ── 第二层：板块内知识点（层级分列 + 先修连线 + 跨板块先修 ghost）── */
      const board = BOARD_BY_CODE[activeBoard]
      const inBoard = points.filter((p) => p.category === activeBoard)
      const inIds = new Set(inBoard.map((p) => p.id))

      /* 跨板块 ghost：被本板块点依赖、但不属于本板块的先修点 */
      const ghostIds = new Set<string>()
      inBoard.forEach((p) =>
        p.prerequisites.forEach((pre) => {
          if (!inIds.has(pre) && pointMap.has(pre)) ghostIds.add(pre)
        })
      )
      const ghosts = [...ghostIds].map((id) => pointMap.get(id)!)
      const hasGhost = ghosts.length > 0

      const padL = 70
      const padR = 60
      const padT = 54
      const padB = 58
      const ghostLaneW = hasGhost ? 120 : 0
      const levelZoneL = padL + ghostLaneW
      const levelZoneR = W - padR
      const colX = (lvl: KsLevel) => levelZoneL + (LEVEL_ORDER[lvl] / 2) * (levelZoneR - levelZoneL)

      const layout = new Map<string, LayoutNode>()

      /* 网格放置一组同列节点：超过 6 个自动分子列，避免竖向糊成一条 */
      const placeColumn = (items: KsPoint[], cx: number, laneW: number) => {
        const n = items.length
        if (!n) return
        const maxPerCol = 6
        const cols = Math.ceil(n / maxPerCol)
        const rows = Math.ceil(n / cols)
        const colGap = cols > 1 ? Math.min(laneW, 150) / cols : 0
        const startX = cx - (colGap * (cols - 1)) / 2
        const top = padT
        const bottom = H - padB
        items.forEach((it, k) => {
          const cc = Math.floor(k / rows)
          const rr = k % rows
          const rowsInCol = cc < cols - 1 ? rows : n - rows * (cols - 1)
          const y = rowsInCol > 1 ? top + ((bottom - top) * rr) / (rowsInCol - 1) : (top + bottom) / 2
          layout.set(it.id, { id: it.id, x: startX + cc * colGap, y })
        })
      }

      LEVELS.forEach((lvl) => {
        const group = inBoard.filter((p) => p.level === lvl)
        const laneW = (levelZoneR - levelZoneL) / 2.4
        placeColumn(group, colX(lvl), laneW)
      })

      /* ghost 竖排在最左泳道 */
      ghosts.forEach((g, i) => {
        const top = padT
        const bottom = H - padB
        const y = ghosts.length > 1 ? top + ((bottom - top) * i) / (ghosts.length - 1) : (top + bottom) / 2
        layout.set(g.id, { id: g.id, x: padL, y })
      })

      const boardNodes = inBoard.map((p) => {
        const pos = layout.get(p.id)!
        const fill = shadeLevel(board.color, p.level, dark)
        const isPassed = passed(p.id)
        return {
          id: p.id,
          name: p.name,
          x: pos.x,
          y: pos.y,
          symbolSize: p.isCore ? 40 : 30,
          itemStyle: {
            color: fill,
            borderColor: isPassed ? t.primary : p.isCore ? withAlpha(t.ink, dark ? 0.5 : 0.35) : withAlpha('#ffffff', dark ? 0.2 : 0.6),
            borderWidth: isPassed ? 3 : p.isCore ? 2.4 : 1.4,
            shadowBlur: 12,
            shadowColor: withAlpha(board.color, 0.4),
          },
          label: {
            show: true,
            position: 'bottom' as const,
            distance: 5,
            color: t.ink,
            fontSize: 11,
            fontWeight: p.isCore ? 700 : 500,
            width: 92,
            overflow: 'truncate' as const,
          },
          _p: p,
          _tt: `<b>${p.name}</b> <span style="opacity:.6">${p.code}</span><br/>层级：${p.level}${p.isCore ? ' · 核心点' : ''}${isPassed ? ' · 已掌握' : ''}<br/>${p.description}`,
        }
      })

      const ghostNodes = ghosts.map((g) => {
        const pos = layout.get(g.id)!
        const gc = BOARD_BY_CODE[g.category]?.color ?? t.inkSoft
        return {
          id: g.id,
          name: `${g.name}`,
          x: pos.x,
          y: pos.y,
          symbolSize: 22,
          itemStyle: {
            color: withAlpha(gc, dark ? 0.28 : 0.2),
            borderColor: withAlpha(gc, 0.7),
            borderWidth: 1.4,
            borderType: 'dashed' as const,
          },
          label: {
            show: true,
            position: 'bottom' as const,
            distance: 4,
            color: t.inkSoft,
            fontSize: 10,
            formatter: `${g.name}\n{b|${BOARD_BY_CODE[g.category]?.short ?? g.category}}`,
            rich: { b: { color: withAlpha(gc, 0.95), fontSize: 9, fontWeight: 700 } },
          },
          _ghost: true,
          _tt: `<b>${g.name}</b>（跨板块先修 · ${BOARD_BY_CODE[g.category]?.name ?? g.category}）<br/>点击跳转其所属板块`,
        }
      })

      const visIds = new Set<string>([...inIds, ...ghostIds])
      const links: any[] = []
      inBoard.forEach((p) => {
        p.prerequisites.forEach((pre) => {
          if (!visIds.has(pre)) return
          const cross = !inIds.has(pre)
          links.push({
            source: pre,
            target: p.id,
            lineStyle: {
              color: withAlpha(cross ? BOARD_BY_CODE[pointMap.get(pre)!.category]?.color ?? t.inkSoft : t.inkSoft, cross ? 0.5 : dark ? 0.4 : 0.32),
              width: cross ? 1.6 : 1.3,
              type: cross ? ('dashed' as const) : ('solid' as const),
              curveness: 0.08,
            },
          })
        })
      })

      option = {
        tooltip: { ...baseTooltip, formatter: (p: any) => (p.dataType === 'node' ? p.data._tt : '') },
        animationDuration: 600,
        animationEasingUpdate: 'quinticInOut',
        series: [
          {
            type: 'graph',
            layout: 'none',
            roam: true,
            draggable: true,
            data: [...ghostNodes, ...boardNodes],
            links,
            edgeSymbol: ['none', 'arrow'],
            edgeSymbolSize: 6,
            emphasis: { focus: 'adjacency', label: { fontWeight: 700 }, lineStyle: { width: 2.6, color: withAlpha(board.color, 0.75) } },
            lineStyle: { opacity: 0.9 },
            labelLayout: { hideOverlap: true },
          } as any,
        ],
      }

      c.setOption(option, true)
      c.off('click')
      c.on('click', (p: any) => {
        if (p.dataType !== 'node') return
        if (p.data._ghost) openPoint(p.data.id)
        else if (p.data._p) setDetail(p.data._p as KsPoint)
      })
    }

    const onResize = () => {
      c.resize()
      setSizeTick((s) => s + 1)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, view, activeBoard, data, masteryStatus, sizeTick])

  /* 头部徽章 */
  const passedCore = points.filter((p) => p.isCore && masteryStatus[p.id] === 'passed').length
  const badges = [
    { label: '知识点', value: points.length },
    { label: '板块', value: BOARD_META.length },
    { label: '已掌握核心', value: `${passedCore}/6`, dot: t.primary },
  ]
  const active = BOARD_BY_CODE[activeBoard]
  const activeCov = coverageByBoard.get(activeBoard)

  return (
    <div className="kg-page">
      <PageHeader
        title="知识图谱"
        highlight="知识图谱"
        tag="Beta"
        subtitle="78 点 / 7 板块 AI 知识体系 · 点板块展开 · 点知识点看详情 · 可缩放 / 拖拽"
        badges={badges}
        crumb="知识图谱"
        onBack={() => onNavigate?.('dashboard')}
      />

      <RevealGroup>
        <RevealItem className="kg-card">
          {/* 面包屑 / 视图切换条 */}
          <div className="kg-bar">
            {view === 'boards' ? (
              <>
                <span className="kg-bar__crumb kg-bar__crumb--here">全部板块</span>
                <span className="kg-bar__hint">点击任一板块气泡展开其知识点</span>
              </>
            ) : (
              <>
                <button className="kg-back" onClick={backToBoards}>← 全部板块</button>
                <span className="kg-bar__sep">/</span>
                <span className="kg-bar__crumb kg-bar__crumb--here" style={{ color: active?.color }}>
                  <span className="kg-bar__dot" style={{ background: active?.color }} />
                  {active?.name}
                </span>
                {activeCov && (
                  <span className="kg-bar__cov">覆盖 {Math.round((activeCov.coveragePct || 0) * 100)}%</span>
                )}
                <span className="kg-legend">
                  {LEVELS.map((lv) => (
                    <span key={lv} className="kg-legend__item">
                      <span
                        className="kg-legend__dot"
                        style={{ background: shadeLevel(active?.color ?? t.primary, lv, false) }}
                      />
                      {lv}
                    </span>
                  ))}
                </span>
              </>
            )}
          </div>

          <div className="kg-graph" ref={ref} />

          {/* 板块快选条（第一层始终可见，点击=展开；名称常驻，不靠 hover）*/}
          {view === 'boards' && (
            <div className="kg-chips">
              {boardSummaries.map((s) => {
                const meta = BOARD_BY_CODE[s.code]
                const cov = coverageByBoard.get(s.code)
                return (
                  <button key={s.code} className="kg-chip" onClick={() => openBoard(s.code)}>
                    <span className="kg-chip__dot" style={{ background: meta?.color }} />
                    <span className="kg-chip__name">{meta?.name}</span>
                    <span className="kg-chip__count">{s.total}</span>
                    {cov && <span className="kg-chip__cov">{Math.round((cov.coveragePct || 0) * 100)}%</span>}
                  </button>
                )
              })}
            </div>
          )}

          <p className="kg-hint">
            {view === 'boards'
              ? '提示：气泡大小 = 板块知识点数量；板块间连线表示跨板块先修依赖。'
              : '提示：从左到右为「入门 → 进阶 → 前沿」；实线为板块内先修，虚线为跨板块先修（点击虚线源节点可跳转）。'}
          </p>

          {/* 第三层：知识点详情面板 */}
          <AnimatePresence>
            {detail && (
              <motion.aside
                className="kg-detail glass-card"
                initial={{ opacity: 0, x: 40 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 40 }}
                transition={{ type: 'spring', stiffness: 320, damping: 32 }}
              >
                <button className="kg-detail__close" onClick={() => setDetail(null)} aria-label="关闭">×</button>
                <div className="kg-detail__tags">
                  <span className="kg-detail__code" style={{ background: withAlpha(BOARD_BY_CODE[detail.category]?.color ?? t.primary, 0.16), color: BOARD_BY_CODE[detail.category]?.color }}>
                    {detail.code}
                  </span>
                  <span className="kg-detail__lv">{detail.level}</span>
                  {detail.isCore && <span className="kg-detail__core">核心点</span>}
                  {masteryStatus[detail.id] === 'passed' && <span className="kg-detail__done">已掌握</span>}
                </div>
                <h3 className="kg-detail__name">{detail.name}</h3>
                <p className="kg-detail__board" style={{ color: BOARD_BY_CODE[detail.category]?.color }}>
                  {BOARD_BY_CODE[detail.category]?.name}
                </p>
                <p className="kg-detail__desc">{detail.description}</p>

                <div className="kg-detail__block">
                  <span className="kg-detail__label">先修知识点</span>
                  {detail.prerequisites.length ? (
                    <div className="kg-detail__pres">
                      {detail.prerequisites.map((id) => {
                        const pre = pointMap.get(id)
                        const c = BOARD_BY_CODE[pre?.category ?? '']?.color ?? t.inkSoft
                        return (
                          <button
                            key={id}
                            className="kg-detail__pre"
                            style={{ borderColor: withAlpha(c, 0.5), color: c }}
                            onClick={() => openPoint(id)}
                          >
                            {pre?.name ?? id}
                          </button>
                        )
                      })}
                    </div>
                  ) : (
                    <span className="kg-detail__none">无（入门起点）</span>
                  )}
                </div>

                {!detail.isCore && (
                  <p className="kg-detail__gen-hint">
                    该点的讲义 / 视频 / 图解等资源由生成引擎<strong>按需生成</strong>——点击下方按钮进入生成与学习。
                  </p>
                )}
                <button className="kg-detail__learn" onClick={() => goLearn(detail)}>
                  {detail.isCore ? '去学习该点 →' : '按需生成学习资源 →'}
                </button>
              </motion.aside>
            )}
          </AnimatePresence>
        </RevealItem>
      </RevealGroup>

    </div>
  )
}
