import { useRef } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

interface CountUpOptions {
  decimals?: number
  duration?: number
  ease?: string
  prefix?: string
  suffix?: string
}

/**
 * GSAP 数字滚动 Hook —— 元素文本从「上一次的值」缓动到目标值。
 * - 首次挂载：0 → target；后续 target 变化：上次显示值 → 新 target（增量平滑，适合实时统计）。
 * - 在 useGSAP 的 layout effect 中渲染，无闪烁；卸载/依赖变更自动清理。
 * 用法：const ref = useCountUp(72.5, { decimals: 1, suffix: '%' }); <span ref={ref} />
 */
export function useCountUp(
  target: number,
  { decimals = 0, duration = 1.4, ease = 'power2.out', prefix = '', suffix = '' }: CountUpOptions = {}
) {
  const ref = useRef<HTMLElement>(null)
  const prev = useRef(0)

  useGSAP(() => {
    const el = ref.current
    if (!el) return
    const counter = { val: prev.current }
    const render = () => {
      el.textContent = `${prefix}${counter.val.toFixed(decimals)}${suffix}`
    }
    render()
    gsap.to(counter, {
      val: target,
      duration,
      ease,
      onUpdate: () => {
        render()
        prev.current = counter.val
      },
      onComplete: () => {
        prev.current = target
      },
    })
  }, [target])

  return ref
}
