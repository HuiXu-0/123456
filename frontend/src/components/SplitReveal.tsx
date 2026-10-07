import { memo, useRef } from 'react'
import type { RefObject } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { SplitText } from 'gsap/SplitText'

gsap.registerPlugin(useGSAP, SplitText)

interface SplitRevealProps {
  text: string
  as?: 'h1' | 'h2' | 'h3' | 'span' | 'p'
  className?: string
  type?: 'chars' | 'words'
  stagger?: number
  duration?: number
  delay?: number
  y?: number
}

/**
 * GSAP SplitText 标题逐字/逐词揭示。
 * 用 memo 包裹：避免父组件重渲染（如学情概览每秒时钟）触发 React 协调而擦掉 SplitText 注入的字符节点。
 * 卸载时 split.revert() 还原 DOM，配合页面切换 mount/unmount 每次进入重新播放。
 */
function SplitRevealBase({
  text,
  as = 'span',
  className,
  type = 'chars',
  stagger = 0.035,
  duration = 0.6,
  delay = 0,
  y = 24,
}: SplitRevealProps) {
  const ref = useRef<HTMLElement>(null)

  useGSAP(() => {
    const el = ref.current
    if (!el) return
    const split = new SplitText(el, { type })
    const targets = type === 'words' ? split.words : split.chars
    gsap.from(targets, {
      y,
      opacity: 0,
      duration,
      ease: 'power3.out',
      stagger,
      delay,
    })
    return () => {
      split.revert()
    }
  }, [text])

  const Tag = as as React.ElementType
  return (
    <Tag ref={ref as RefObject<HTMLElement>} className={className}>
      {text}
    </Tag>
  )
}

export default memo(SplitRevealBase)
