import { useEffect, useRef, useState } from 'react'
import { openInstantTutor } from '../services/tutorBus'
import './InstantTutorExtras.css'

/**
 * 选中即问（B-2）：在正文里选中一段文字，就近浮出「就这段问 AI」，点击把这句话作为上下文
 * 发起即时辅导，让针对性具体到句子。
 *
 * 两种作用域：
 *  - 传入 `containerRef`：仅监听该容器内的选区（旧的按页接法，向后兼容）；
 *  - 不传（全局模式）：升到 App 顶层监听全站选区，但只在「正文内容区」（.markdown-body /
 *    [data-ask-content]）触发，并排除输入框 / 文本域 / 代码块 / 按钮等交互控件，避免误弹。
 */
interface Props {
  /** 监听选区的容器；省略则进入全局模式（正文内容区通用触发） */
  containerRef?: React.RefObject<HTMLElement>
}

/** 全局模式下允许触发的正文内容区 */
const ALLOW_SELECTOR = '.markdown-body, [data-ask-content]'
/** 交互控件 / 代码区 —— 落在其中的选中一律不弹 */
const EXCLUDE_SELECTOR =
  'input, textarea, select, button, [contenteditable], [contenteditable=true], .cm-editor, .code-sandbox, .md-codeblock, pre, code, [data-no-ask]'

/** 取选区端点所在的元素（文本节点回退到父元素）。 */
function elementOf(node: Node | null): Element | null {
  if (!node) return null
  return node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as Element)
}

export default function SelectionAskBubble({ containerRef }: Props) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const textRef = useRef('')

  useEffect(() => {
    const onUp = () => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        setPos(null)
        return
      }
      const text = sel.toString().trim()
      const anchor = sel.anchorNode
      const focus = sel.focusNode
      if (text.length < 4 || !anchor) {
        setPos(null)
        return
      }

      if (containerRef) {
        // 容器模式：两端都须落在容器内
        const container = containerRef.current
        if (!container || !container.contains(anchor) || (focus && !container.contains(focus))) {
          setPos(null)
          return
        }
      } else {
        // 全局模式：命中正文内容区、且不在交互控件 / 代码区内才弹
        const a = elementOf(anchor)
        const f = elementOf(focus)
        if (!a || a.closest(EXCLUDE_SELECTOR) || (f && f.closest(EXCLUDE_SELECTOR))) {
          setPos(null)
          return
        }
        if (!a.closest(ALLOW_SELECTOR) || (f && !f.closest(ALLOW_SELECTOR))) {
          setPos(null)
          return
        }
      }

      const rect = sel.getRangeAt(0).getBoundingClientRect()
      if (!rect.width && !rect.height) {
        setPos(null)
        return
      }
      textRef.current = text
      setPos({ x: rect.left + rect.width / 2, y: rect.top })
    }
    // 另起一次按下（开始新选择 / 点击别处）先收起，避免气泡滞留
    const onDown = () => setPos(null)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('mousedown', onDown)
    }
  }, [containerRef])

  if (!pos) return null

  const ask = () => {
    const t = textRef.current.replace(/\s+/g, ' ').slice(0, 120)
    openInstantTutor({
      question: `这段内容我没太看懂：“${t}”，帮我把它讲清楚`,
      autoAsk: true,
    })
    setPos(null)
    window.getSelection()?.removeAllRanges()
  }

  return (
    <button
      type="button"
      className="sel-ask-bubble"
      style={{ left: pos.x, top: pos.y }}
      // 阻止默认 + 冒泡：点击气泡时不清除选区、不触发上面的收起逻辑
      onMouseDown={(e) => {
        e.preventDefault()
        e.stopPropagation()
      }}
      onClick={ask}
    >
      💡 就这段问 AI
    </button>
  )
}
