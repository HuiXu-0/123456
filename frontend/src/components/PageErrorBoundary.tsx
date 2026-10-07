import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  /** 变化即重置错误态（传 currentPage：切换页面自动复位，无需整页刷新） */
  resetKey?: unknown
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * 路由页渲染兜底（issue#2/3）。
 *
 * 此前 frontend 无任何 ErrorBoundary：任一视图渲染期抛错（如后端 overview 回包缺字段、
 * 快照结构异常）都会冲掉整棵 React 树 → 内容区白屏，且只有整页刷新才恢复（回到初始态）。
 * 该边界把渲染错误收敛在内容区，给出「重试 / 刷新」兜底，并随 resetKey（currentPage）
 * 变化自动复位——切到别的页即恢复，不再依赖整页刷新。
 */
export default class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(prev: Props) {
    // 路由切换（resetKey 变化）→ 清除上一页的错误态，渲染新页
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[page] 视图渲染失败', error, info.componentStack)
  }

  private handleRetry = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      return (
        <div className="page-error" role="alert">
          <div className="page-error__icon" aria-hidden="true">⚠</div>
          <h2 className="page-error__title">该视图加载时出了点问题</h2>
          <p className="page-error__desc">
            内容渲染中断，已被安全拦截（不影响其他页面）。可重试加载，或切换到其他页面后再返回。
          </p>
          <div className="page-error__actions">
            <button className="page-error__btn page-error__btn--primary" onClick={this.handleRetry}>
              重试加载
            </button>
            <button className="page-error__btn" onClick={() => window.location.reload()}>
              刷新页面
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
