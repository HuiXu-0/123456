import type { RefObject } from 'react'
import { useGSAP } from '@gsap/react'
import { gsap } from 'gsap'

gsap.registerPlugin(useGSAP)

/**
 * 滚动渐显：scope 内带 [data-reveal] 的元素，进入视口时由 GSAP 淡入上浮。
 * 用 IntersectionObserver 触发（不依赖具体滚动容器，规避 .app__main / window 滚动模型歧义）。
 * 若元素初始即在视口内，则直接渐入；否则滚动到时再渐入。
 */
export function useScrollReveal(scope: RefObject<HTMLElement>) {
  useGSAP(() => {
    const host = scope.current
    if (!host) return
    const els = Array.from(host.querySelectorAll<HTMLElement>('[data-reveal]'))
    if (!els.length) return

    gsap.set(els, { opacity: 0, y: 40 })

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            gsap.to(e.target, { opacity: 1, y: 0, duration: 0.7, ease: 'power2.out' })
            io.unobserve(e.target)
          }
        })
      },
      { threshold: 0.15 }
    )
    els.forEach((el) => io.observe(el))

    return () => io.disconnect()
  }, { scope })
}
