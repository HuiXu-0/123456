import { useEffect, useRef, useState } from 'react'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import { downloadSvg, downloadSvgAsPng } from '../utils/resourceExport'
import { buildFigureCaption, deriveTopic, nextFigureNumber } from '../utils/figureCaption'
import '../styles/paperFigure.css'

const transformer = new Transformer()

// 论文级学术配色：按层级由深到浅的冷调石板蓝单色阶（根节点最深），克制、层级分明，
// 观感对齐 SCI 示意图；字体由 paperFigure.css 统一为论文字体栈（拉丁 Times / 中文衬线）。
const PAPER_PALETTE = ['#26333d', '#3c5261', '#5a7284', '#8598a6']

/**
 * 思维导图：把 Markdown 大纲渲染成 markmap（SVG）。
 * 传入 downloadName 时，在导图上方显示「下载 SVG / PNG」工具条（导出当前已渲染的导图，不重新生成）。
 */
export default function MindMap({
  markdown,
  downloadName,
  caption,
}: {
  markdown: string
  downloadName?: string
  /** 自定义图注（覆盖默认「图 N　主题 · 结构化思维导图」）；不传则据 downloadName 自动生成。 */
  caption?: string
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const mmRef = useRef<Markmap | null>(null)
  const [ready, setReady] = useState(false)
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
    : caption ?? (figNo > 0 ? buildFigureCaption('mindmap', deriveTopic(downloadName), figNo) : '')

  useEffect(() => {
    if (!svgRef.current) return
    const { root } = transformer.transform(markdown)
    if (!mmRef.current) {
      mmRef.current = Markmap.create(svgRef.current, {
        autoFit: true,
        duration: 400,
        spacingVertical: 12,
        spacingHorizontal: 64,
        paddingX: 24,
        fitRatio: 0.92,
        color: (node) => {
          // 按层级配学术冷调石板蓝单色阶（根最深，越深层越浅）
          return PAPER_PALETTE[(node.state?.depth ?? 0) % PAPER_PALETTE.length]
        },
      })
    }
    mmRef.current.setData(root)
    // 等布局与容器尺寸就绪后再 fit，避免裁切/偏移
    requestAnimationFrame(() => {
      mmRef.current?.fit()
      setReady(true)
    })

    return () => {
      mmRef.current?.destroy()
      mmRef.current = null
      setReady(false)
    }
  }, [markdown])

  const svg = <svg ref={svgRef} className="mindmap-svg" />
  if (!downloadName) return svg

  return (
    <figure className="paper-figure">
      <div className="resource-export-block">
        <div className="lecture-export">
          <span className="lecture-export__label">下载导图：</span>
          <button
            type="button"
            className="lecture-export__btn"
            onClick={() => {
              if (svgRef.current) downloadSvg(svgRef.current, downloadName, 'start', captionText)
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
              if (svgRef.current) void downloadSvgAsPng(svgRef.current, downloadName, 'start', captionText)
            }}
            disabled={!ready}
            title="导出为位图（.png，便于插入文档/分享）"
          >
            <span aria-hidden="true">🖼</span> PNG
          </button>
        </div>
        {svg}
      </div>
      <figcaption className="paper-figure__caption">{captionText}</figcaption>
    </figure>
  )
}
