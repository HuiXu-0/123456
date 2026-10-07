import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { downloadSvg, downloadSvgAsPng } from '../utils/resourceExport'
import { sanitizeMermaid } from '../utils/mermaidSanitize'
import { buildFigureCaption, deriveTopic, nextFigureNumber } from '../utils/figureCaption'
import '../styles/paperFigure.css'

// 论文级学术主题：冷灰蓝线条/文字 + 近白冷灰节点底 + 论文字体栈（拉丁 Times New Roman / 中文衬线）。
// fontFamily 用 var(--font-paper)：在线渲染于文档内可解析；导出时 resourceExport 读 computed style
// 取到解析后的字体族，保证 SVG/PNG 也是论文级字体。深浅（sage/ink）主题的图解都落在浅底卡片，共用此配色。
mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  // 关键：解析 / 渲染失败时**不要**把 mermaid 自带的报错图（"error in text … version 11.x"）
  // 注入进 DOM——否则该错误图会漏到页面背景形成满屏「串图」。失败一律走本组件兜底 UI。
  suppressErrorRendering: true,
  themeVariables: {
    fontFamily: 'var(--font-paper)',
    fontSize: '15px',
    primaryColor: '#f5f7f9',
    primaryBorderColor: '#35434f',
    primaryTextColor: '#1f2a33',
    secondaryColor: '#eef1f4',
    secondaryBorderColor: '#5c6b78',
    secondaryTextColor: '#1f2a33',
    tertiaryColor: '#f8fafb',
    tertiaryBorderColor: '#8598a6',
    tertiaryTextColor: '#1f2a33',
    lineColor: '#5c6b78',
    textColor: '#1f2a33',
    mainBkg: '#f5f7f9',
    nodeBorder: '#35434f',
    clusterBkg: '#f0f3f5',
    clusterBorder: '#c3ccd3',
    edgeLabelBackground: '#ffffff',
    titleColor: '#1f2a33',
  },
})

let idSeq = 0

/**
 * Mermaid 图解渲染（流程图 / 架构图等）。
 * 传入 downloadName 时，在图解上方显示「下载 SVG / PNG」工具条（导出当前已渲染的图，不重新生成）；
 * 不传则保持原样（讲义内嵌图解等场景零变化）。
 */
export default function MermaidDiagram({
  chart,
  downloadName,
  caption,
}: {
  chart: string
  downloadName?: string
  /** 自定义图注（覆盖默认「图 N　主题 · 知识脉络图解」）；不传则据 downloadName 自动生成。 */
  caption?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  // 仅在「资源型图解」（有 downloadName / 显式 caption）时配图号与图注；讲义内嵌图解不编号，保持原样。
  const showCaption = !!(caption || downloadName)
  // 图号在 effect 中一次性领取（用 ref 去重），避免 StrictMode 下重复挂载导致编号跳号。
  const [figNo, setFigNo] = useState(0)
  const figNoAssigned = useRef(false)
  useEffect(() => {
    if (showCaption && !caption && !figNoAssigned.current) {
      figNoAssigned.current = true
      setFigNo(nextFigureNumber())
    }
  }, [showCaption, caption])
  const captionText = !showCaption
    ? ''
    : caption ?? (figNo > 0 ? buildFigureCaption('diagram', deriveTopic(downloadName), figNo) : '')

  useEffect(() => {
    let cancelled = false
    setReady(false)
    setErr(null)
    const id = `mmd-${idSeq++}`
    // 渲染前清洗节点标签特殊字符（数学公式 / <、>、() 、[] 等），降低 Parse error；
    // 再用 mermaid.parse(suppressErrors) 先行校验——非法语法只返回 false、绝不注入报错图，
    // 从而任何失败都被本组件兜底 UI 收住，绝不漏到页面背景。
    const run = async () => {
      try {
        const src = sanitizeMermaid(chart)
        const ok = await mermaid.parse(src, { suppressErrors: true })
        if (ok === false) throw new Error('图解语法无法解析')
        const { svg } = await mermaid.render(id, src)
        if (!cancelled && ref.current) {
          ref.current.innerHTML = svg
          setReady(true)
        }
      } catch (e) {
        if (!cancelled) setErr(String((e as Error)?.message || e))
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [chart])

  if (err) return <div className="mermaid-error">图解渲染失败：{err}</div>

  const diagram = <div className="mermaid-diagram" ref={ref} />
  // 讲义内嵌图解等（无 downloadName）：仅经全局主题套用论文字体/配色，结构不变、不加图注。
  if (!downloadName) return diagram

  const getSvg = () => ref.current?.querySelector('svg') as SVGSVGElement | null

  return (
    <figure className="paper-figure">
      <div className="resource-export-block">
        <div className="lecture-export">
          <span className="lecture-export__label">下载图解：</span>
          <button
            type="button"
            className="lecture-export__btn"
            onClick={() => {
              const svg = getSvg()
              if (svg) downloadSvg(svg, downloadName, 'middle', captionText)
            }}
            disabled={!ready}
            title="下载矢量图（.svg，可无损缩放）"
          >
            <span aria-hidden="true">⬇</span> SVG
          </button>
          <button
            type="button"
            className="lecture-export__btn lecture-export__btn--pdf"
            onClick={() => {
              const svg = getSvg()
              if (svg) void downloadSvgAsPng(svg, downloadName, 'middle', captionText)
            }}
            disabled={!ready}
            title="导出为位图（.png，便于插入文档/分享）"
          >
            <span aria-hidden="true">🖼</span> PNG
          </button>
        </div>
        {diagram}
      </div>
      <figcaption className="paper-figure__caption">{captionText}</figcaption>
    </figure>
  )
}
