import { useEffect, useState } from 'react'

/**
 * ECharts 主题取色：图表渲染在 canvas 上，吃不到 CSS 变量，
 * 必须运行时读取 :root 的护眼令牌再 setOption，并在 data-theme 变化时重渲染。
 */
export interface ChartTokens {
  primary: string
  primaryD: string
  accent: string
  ink: string
  inkSoft: string
  line: string
  surface: string
  surface2: string
  /** 类别色板（从主题变量取，杜绝 ECharts 默认蓝色板）*/
  palette: string[]
}

function read(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

export function readChartTokens(): ChartTokens {
  const primary = read('--primary', '#5B7F6E')
  const primaryD = read('--primary-d', '#46624F')
  const accent = read('--accent', '#C58940')
  const ink = read('--ink', '#2E332E')
  const inkSoft = read('--ink-soft', '#5C6359')
  const line = read('--line', '#DEDCD0')
  const surface = read('--surface', '#FAF9F4')
  const surface2 = read('--surface-2', '#EBEAE0')
  return {
    primary,
    primaryD,
    accent,
    ink,
    inkSoft,
    line,
    surface,
    surface2,
    palette: [primary, accent, primaryD, inkSoft],
  }
}

/** hex(#rrggbb) → rgba 字符串；已是 rgb/rgba 等则原样返回 */
export function withAlpha(color: string, a: number): string {
  const h = color.replace('#', '').trim()
  if (h.length < 6 || /[^0-9a-fA-F]/.test(h.slice(0, 6))) return color
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${a})`
}

/** 监听 data-theme 变化，返回最新图表令牌（作为图表 useEffect 依赖触发重渲染）*/
export function useChartTokens(): ChartTokens {
  const [tokens, setTokens] = useState<ChartTokens>(readChartTokens)
  useEffect(() => {
    const el = document.documentElement
    const obs = new MutationObserver(() => setTokens(readChartTokens()))
    obs.observe(el, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [])
  return tokens
}
