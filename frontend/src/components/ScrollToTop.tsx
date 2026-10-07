import { useLayoutEffect } from 'react'

interface Props {
  /** 变化即触发归零（此处传 currentPage：页面切换信号） */
  dep: unknown
  /** 主滚动容器引用（.app__main） */
  containerRef: React.RefObject<HTMLElement>
}

/**
 * 路由（页面）切换时把主滚动容器归零。
 * 本应用无 react-router，导航靠 currentPage 状态机 + AnimatePresence 重挂载页面；
 * 切换时上一页的滚动位置会残留在主容器（.app__main）上，导致新页面停在中部。
 * 监听 dep 变化，把主容器与窗口滚动一并归零。
 */
export default function ScrollToTop({ dep, containerRef }: Props) {
  useLayoutEffect(() => {
    containerRef.current?.scrollTo({ top: 0, left: 0, behavior: 'auto' })
    if (typeof window !== 'undefined') window.scrollTo(0, 0)
  }, [dep, containerRef])
  return null
}
