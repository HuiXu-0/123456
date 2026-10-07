import type { CSSProperties, RefObject } from 'react'
import { useCountUp } from '../hooks/useCountUp'

interface CountUpProps {
  value: number
  decimals?: number
  duration?: number
  prefix?: string
  suffix?: string
  className?: string
  style?: CSSProperties
}

/**
 * GSAP 数字滚动组件。渲染空 span 由 GSAP 接管文本，避免父组件重渲染（如时钟每秒刷新）覆盖动画值。
 * 用法：<CountUp value={72.5} decimals={1} className="..." />
 */
export default function CountUp({ value, decimals, duration, prefix, suffix, className, style }: CountUpProps) {
  const ref = useCountUp(value, { decimals, duration, prefix, suffix })
  return <span ref={ref as RefObject<HTMLSpanElement>} className={className} style={style} />
}
