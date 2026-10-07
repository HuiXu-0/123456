import { Component, lazy, type ComponentType, type ReactNode } from 'react'
import './InlineErrorBoundary.css'

/**
 * 元素级渲染兜底（与页面级 PageErrorBoundary 相对）。
 *
 * 根因背景：讲义 / 文档学习产出里内嵌的懒加载组件（MermaidDiagram / VideoLecture / MindMap）
 * 一旦 chunk 拉取失败或渲染期抛错，React 会沿树上抛到 PageErrorBoundary，把**整个视图**
 * 换成错误页（实测堆栈：`Failed to fetch dynamically imported module … at Lazy at Suspense
 * … at DocumentLearning`）。本边界收敛在单个元素：坏的只有这一块，页面其余内容照常。
 */
export default class InlineErrorBoundary extends Component<
  { label?: string; fallback?: ReactNode; children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.warn(`[inline] 「${this.props.label ?? '内容模块'}」渲染降级（不影响页面其余部分）`, error)
  }

  private retry = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      if (this.props.fallback !== undefined) return this.props.fallback
      return (
        <div className="inline-fallback" role="note">
          <span className="inline-fallback__icon" aria-hidden="true">⚠</span>
          <span>「{this.props.label ?? '内容模块'}」渲染失败，已降级跳过（页面其余内容不受影响）。</span>
          <button type="button" className="inline-fallback__retry" onClick={this.retry}>
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

/**
 * 抗瞬断的懒加载：import() 失败自动重试 2 次（各退避 400ms），仍失败则**解析为一个
 * 内联降级组件**而非 reject——React.lazy 会缓存 reject 结果导致该元素永久性把整页打崩，
 * 这里保证 lazy 永不 reject，chunk 失败只降级该元素本身。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- 与 React.lazy 的泛型签名保持一致
export function lazySafe<T extends ComponentType<any>>(
  load: () => Promise<{ default: T }>,
  label: string
) {
  return lazy(async () => {
    let lastErr: unknown
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        return await load()
      } catch (e) {
        lastErr = e
        await new Promise((r) => setTimeout(r, 400))
      }
    }
    console.error(`[lazy] 模块「${label}」加载失败（已重试）`, lastErr)
    const Fallback = (() => (
      <div className="inline-fallback" role="note">
        <span className="inline-fallback__icon" aria-hidden="true">⚠</span>
        <span>「{label}」模块加载失败，已降级跳过。请检查网络或刷新页面重试。</span>
        <button type="button" className="inline-fallback__retry" onClick={() => window.location.reload()}>
          刷新页面
        </button>
      </div>
    )) as unknown as T
    return { default: Fallback }
  })
}
