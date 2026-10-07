/**
 * 学习资源导出工具（纯前端，不重新请求生成；复用「当前已渲染好」的产物）。
 *
 * - 图解（mermaid）/ 思维导图（markmap）：把在线 SVG 导出为 .svg / .png。
 * - 代码实操（本地沙箱 CodeSandbox）：把代码文件内容按真实后缀导出为单文件。
 *
 * 为什么导出前要把 <foreignObject> 降级成原生 <text>：
 *   mermaid（htmlLabels 默认开）与 markmap 都把节点文字渲染在 <foreignObject> 的 HTML 里。
 *   而浏览器出于安全，<img> 加载的 SVG 不会渲染 foreignObject 的 HTML —— 直接转 canvas 会
 *   得到「有框线无文字」的空图。这里在导出克隆体里把 foreignObject 文字替换为原生 <text>
 *   （颜色/字号从在线 DOM 的 computed style 取），保证 .svg 直接打开与 .png 位图里文字都可见。
 *   在线渲染本身不受影响（只动克隆体）。
 */

import { sanitizeFilename, triggerDownload } from './lectureExport'
import { PAPER_FONT_STACK } from './figureCaption'

const SVG_NS = 'http://www.w3.org/2000/svg'
const XLINK_NS = 'http://www.w3.org/1999/xlink'
// 独立导出的 SVG 脱离页面、无 :root 变量，图注字体必须用字面量字体栈（与 --font-paper 同源）。
const CAPTION_FONT_SIZE = 15
const CAPTION_PAD = 46 // 图注区高度（分隔线 + 文字 + 上下留白）

/** 把克隆体里的 foreignObject 文字降级为原生 <text>（位置/颜色/字体取自在线 DOM）。 */
function inlineForeignObjects(clone: SVGSVGElement, live: SVGSVGElement, anchor: 'start' | 'middle'): void {
  const clonedFOs = Array.from(clone.querySelectorAll('foreignObject'))
  const liveFOs = Array.from(live.querySelectorAll('foreignObject'))
  clonedFOs.forEach((fo, i) => {
    const liveFo = liveFOs[i] ?? null
    const text = ((liveFo ?? fo).textContent ?? '').replace(/\s+/g, ' ').trim()
    if (!text) {
      fo.remove()
      return
    }
    const x = parseFloat(fo.getAttribute('x') || '0')
    const y = parseFloat(fo.getAttribute('y') || '0')
    const w = parseFloat(fo.getAttribute('width') || '0')
    const h = parseFloat(fo.getAttribute('height') || '0')
    // 克隆体不在文档树里取不到 computed style，故从在线节点上取真实样式
    const probe =
      (liveFo?.querySelector('div, span, p') as HTMLElement | null) ?? (liveFo as unknown as HTMLElement | null)
    const cs = probe ? getComputedStyle(probe) : null
    const t = clone.ownerDocument.createElementNS(SVG_NS, 'text')
    t.setAttribute('x', String(anchor === 'middle' ? x + w / 2 : x + Math.min(6, w * 0.06)))
    t.setAttribute('y', String(y + h / 2))
    t.setAttribute('text-anchor', anchor)
    t.setAttribute('dominant-baseline', 'central')
    t.setAttribute('fill', cs?.color || '#1e293b')
    t.setAttribute('font-family', cs?.fontFamily || 'inherit')
    t.setAttribute('font-size', cs?.fontSize || '14px')
    t.setAttribute('font-weight', cs?.fontWeight || '500')
    t.textContent = text
    fo.replaceWith(t)
  })
}

/** 由在线 SVG 生成「自包含、可脱离页面渲染」的 SVG 串与像素尺寸；caption 非空时在图下方嵌入学术图注。 */
function buildSvgString(
  live: SVGSVGElement,
  anchor: 'start' | 'middle',
  caption?: string,
): { xml: string; width: number; height: number } {
  const clone = live.cloneNode(true) as SVGSVGElement
  inlineForeignObjects(clone, live, anchor)

  // 尺寸优先用 viewBox（覆盖完整内容），退化到渲染框 / width 属性
  const vb = live.viewBox?.baseVal
  const hasVb = !!vb && vb.width > 0 && vb.height > 0
  const rect = live.getBoundingClientRect()
  const width = Math.max(1, Math.round(hasVb ? vb!.width : rect.width || parseFloat(live.getAttribute('width') || '') || 800))
  const baseHeight = Math.max(
    1,
    Math.round(hasVb ? vb!.height : rect.height || parseFloat(live.getAttribute('height') || '') || 600),
  )
  const cap = (caption ?? '').trim()
  const capPad = cap ? CAPTION_PAD : 0
  const height = baseHeight + capPad

  // 内容盒（viewBox 坐标系）——图注区在其正下方扩展 capPad
  const ox = hasVb ? vb!.x : 0
  const oy = hasVb ? vb!.y : 0
  const bw = hasVb ? vb!.width : width
  const bh = (hasVb ? vb!.height : baseHeight) + capPad

  clone.setAttribute('xmlns', SVG_NS)
  clone.setAttribute('xmlns:xlink', XLINK_NS)
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  clone.setAttribute('viewBox', `${ox} ${oy} ${bw} ${bh}`)

  // 白底矩形：避免透明背景下深色文字在图片查看器里看不清（含图注区）
  const bg = clone.ownerDocument.createElementNS(SVG_NS, 'rect')
  bg.setAttribute('x', String(ox))
  bg.setAttribute('y', String(oy))
  bg.setAttribute('width', String(bw))
  bg.setAttribute('height', String(bh))
  bg.setAttribute('fill', '#ffffff')
  clone.insertBefore(bg, clone.firstChild)

  // 学术图注：细分隔线 + 居中小字（论文字体栈），置于内容正下方
  if (cap) {
    const doc = clone.ownerDocument
    const lineY = oy + bh - capPad + 14
    const sep = doc.createElementNS(SVG_NS, 'line')
    sep.setAttribute('x1', String(ox + bw * 0.14))
    sep.setAttribute('x2', String(ox + bw * 0.86))
    sep.setAttribute('y1', String(lineY))
    sep.setAttribute('y2', String(lineY))
    sep.setAttribute('stroke', '#c3ccd3')
    sep.setAttribute('stroke-width', '1')
    clone.appendChild(sep)

    const t = doc.createElementNS(SVG_NS, 'text')
    t.setAttribute('x', String(ox + bw / 2))
    t.setAttribute('y', String(lineY + 20))
    t.setAttribute('text-anchor', 'middle')
    t.setAttribute('dominant-baseline', 'central')
    t.setAttribute('fill', '#445059')
    t.setAttribute('font-family', PAPER_FONT_STACK)
    t.setAttribute('font-size', `${CAPTION_FONT_SIZE}px`)
    t.textContent = cap
    clone.appendChild(t)
  }

  const xml = new XMLSerializer().serializeToString(clone)
  return { xml, width, height }
}

/** 导出在线 SVG 为 .svg 文件（文件名如「图解-知识点.svg」）；caption 非空时图下方带学术图注。 */
export function downloadSvg(
  svg: SVGSVGElement,
  baseName: string,
  anchor: 'start' | 'middle' = 'middle',
  caption?: string,
): void {
  const { xml } = buildSvgString(svg, anchor, caption)
  const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n${xml}`], {
    type: 'image/svg+xml;charset=utf-8',
  })
  triggerDownload(blob, `${sanitizeFilename(baseName)}.svg`)
}

/** 导出在线 SVG 为 .png（SVG → Image → canvas → PNG，默认 2 倍清晰度）；caption 非空时带学术图注。 */
export async function downloadSvgAsPng(
  svg: SVGSVGElement,
  baseName: string,
  anchor: 'start' | 'middle' = 'middle',
  caption?: string,
  scale = 2,
): Promise<void> {
  const { xml, width, height } = buildSvgString(svg, anchor, caption)
  const url = URL.createObjectURL(new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const img = new Image()
    img.decoding = 'async'
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('SVG 转图片失败'))
      img.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(width * scale))
    canvas.height = Math.max(1, Math.round(height * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('无法创建画布上下文')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    const pngBlob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('PNG 编码失败'))), 'image/png'),
    )
    triggerDownload(pngBlob, `${sanitizeFilename(baseName)}.png`)
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** 后缀 → MIME，决定下载代码文件的 Content-Type。 */
const CODE_MIME: Record<string, string> = {
  js: 'text/javascript',
  mjs: 'text/javascript',
  ts: 'text/typescript',
  tsx: 'text/typescript',
  jsx: 'text/javascript',
  py: 'text/x-python',
  html: 'text/html',
  css: 'text/css',
  json: 'application/json',
  java: 'text/x-java-source',
  cpp: 'text/x-c++src',
  c: 'text/x-csrc',
  go: 'text/x-go',
  rs: 'text/rust',
  md: 'text/markdown',
  txt: 'text/plain',
}

/**
 * 把代码内容按真实后缀导出为单文件（如「代码-知识点-index.js」）。
 * 仅清洗文件名主名，保留后缀以便正常运行/识别。
 */
export function downloadCodeFile(content: string, filename: string): void {
  const dot = filename.lastIndexOf('.')
  const stem = dot > 0 ? filename.slice(0, dot) : filename
  const ext = dot > 0 ? filename.slice(dot + 1).toLowerCase() : ''
  const safeName = sanitizeFilename(stem) + (ext ? `.${ext}` : '')
  const blob = new Blob([content ?? ''], { type: `${CODE_MIME[ext] || 'text/plain'};charset=utf-8` })
  triggerDownload(blob, safeName)
}
