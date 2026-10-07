/**
 * 讲义导出工具（纯前端，不重新请求生成；导出"当前已渲染好"的内容）。
 *
 * - exportLectureMarkdown：直接把讲义 markdown 原文下载为 .md（最稳，内容完整）。
 * - exportLectureToPdf：把"已渲染"的讲义 DOM 克隆进一个自包含打印窗口（注入干净打印样式 +
 *   页面里已加载的 KaTeX 样式），调浏览器原生打印 → 用户「另存为 PDF」。
 *
 * 为什么走打印窗口而非 html2canvas/jsPDF：本项目主题大量使用 CSS `color-mix()`，
 * 而 html2canvas 无法解析 `color-mix()/oklch()`（会抛 "unsupported color function" 直接失败）。
 * 打印窗口用浏览器原生渲染器，对公式(KaTeX HTML)、图解(mermaid SVG)、图片、表格保真最好，
 * 且文字可选中、原生分页、无跨域污染问题。
 */

/** 文件名清洗：去除各操作系统非法字符，折叠空白，限长，保底名。 */
export function sanitizeFilename(name: string): string {
  const cleaned = (name || '')
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, '') // 非法字符
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
  return cleaned || '讲义'
}

/** 触发浏览器下载一个 Blob。 */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 给浏览器留出读取 url 的时间再回收
  window.setTimeout(() => URL.revokeObjectURL(url), 1500)
}

/** 导出讲义 markdown 原文为 .md（文件名如「讲义-知识点-难度.md」）。 */
export function exportLectureMarkdown(content: string, baseName: string): void {
  const blob = new Blob([content ?? ''], { type: 'text/markdown;charset=utf-8' })
  triggerDownload(blob, `${sanitizeFilename(baseName)}.md`)
}

/** HTML 文本转义（用于 <title> 与正文标题，避免知识点名里的特殊字符破坏结构）。 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 从当前页面已加载的样式表里收集 KaTeX 规则文本，注入打印窗口让公式带样式渲染。 */
function collectKatexCss(): string {
  const chunks: string[] = []
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const href = (sheet as CSSStyleSheet).href || ''
      const rules = (sheet as CSSStyleSheet).cssRules
      if (!rules) continue
      // 命中 katex 样式表（Vite 打包后同源可访问）整表搬运；否则跳过。
      const isKatexSheet =
        href.includes('katex') || Array.from(rules).some((r) => /\.katex/.test((r as CSSRule).cssText || ''))
      if (!isKatexSheet) continue
      for (const r of Array.from(rules)) chunks.push((r as CSSRule).cssText)
    } catch {
      // 跨域样式表读取 cssRules 会抛错 → 安全跳过（公式仍以可读文本呈现）
    }
  }
  return chunks.join('\n')
}

/** 打印窗口里的干净排版（用纯 hex 颜色，规避主题里的 color-mix；白底深字、表格描边、代码/图片不溢出）。 */
const PRINT_CSS = `
@page { margin: 16mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #ffffff; }
.print-root {
  max-width: 760px; margin: 0 auto; padding: 8px;
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  color: #1f2433; font-size: 14px; line-height: 1.8; -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.print-meta { color: #8a93a6; font-size: 12px; margin: 0 0 18px; padding-bottom: 10px; border-bottom: 1px solid #e6e9f0; }
.print-root h1, .resource-title { font-size: 24px; font-weight: 800; color: #11131c; margin: 0 0 16px; }
.print-root h2, .section-title { font-size: 19px; font-weight: 700; color: #1f2433; margin: 24px 0 10px; padding-left: 10px; border-left: 4px solid #2563eb; }
.print-root h3, .subsection-title { font-size: 16px; font-weight: 600; color: #2b3040; margin: 18px 0 8px; }
.print-root p, .content-paragraph { margin: 0 0 10px; }
.print-root ul, .print-root ol, .content-list, .ordered-list { margin: 8px 0 14px; padding-left: 1.6em; }
.print-root li { margin: 4px 0; }
.print-root strong { color: #11131c; font-weight: 700; }
.print-root del { color: #6b7280; }
.print-root blockquote, .content-quote { margin: 14px 0; padding: 10px 16px; background: #f1f5ff; border-left: 4px solid #93b4f5; color: #475067; border-radius: 0 6px 6px 0; }
.print-root code, .md-inline-code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .88em; background: #eef2ff; color: #3730a3; padding: 2px 6px; border-radius: 5px; }
.print-root pre, .md-codeblock { border-radius: 10px; overflow: hidden; margin: 14px 0; }
.print-root pre, .print-root pre *, .md-codeblock, .md-codeblock * { white-space: pre-wrap !important; word-break: break-word; }
.print-root table, .md-table { border-collapse: collapse; width: 100%; margin: 0; font-size: 13px; }
.md-table-wrap { margin: 14px 0; overflow: visible; border: none; }
.print-root th, .print-root td, .md-table th, .md-table td { border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; vertical-align: top; color: #1f2433; white-space: normal; }
.print-root th, .md-table th { background: #f1f5f9; font-weight: 700; }
.print-root tr:nth-child(even) td, .md-table tr:nth-child(even) td { background: #f8fafc; }
.print-root img, .md-image, .print-root svg { max-width: 100%; height: auto; }
.md-image-fallback { color: #94a3b8; font-size: 13px; }
h1, h2, h3 { page-break-after: avoid; }
table, pre, blockquote, .md-table-wrap, .md-codeblock, img, svg { page-break-inside: avoid; }
`

/**
 * 把"已渲染好的讲义 DOM"导出为 PDF（经浏览器打印 → 另存为 PDF）。
 * @param sourceEl 已渲染的讲义容器（如 .markdown-body 节点）
 * @param title    文件/文档标题（如「讲义-神经网络基础-初级」），决定另存为 PDF 的建议文件名
 * @param meta     可选的副标题信息（知识点 / 难度 / 导出时间），展示在 PDF 顶部
 * @returns 是否成功打开打印窗口（被浏览器拦截弹窗时返回 false）
 */
export function exportLectureToPdf(sourceEl: HTMLElement, title: string, meta?: string): boolean {
  const safeTitle = sanitizeFilename(title)
  const inner = sourceEl.cloneNode(true) as HTMLElement
  // 去掉非内容节点（如选词浮泡、再生成遮罩、加载占位），只留讲义正文
  inner
    .querySelectorAll('.sel-ask-bubble, .lecture-regen, .resource-loading, [data-export-ignore]')
    .forEach((n) => n.remove())

  const katexCss = collectKatexCss()
  const metaHtml = meta ? `<div class="print-meta">${escapeHtml(meta)}</div>` : ''
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${escapeHtml(safeTitle)}</title>
<style>${PRINT_CSS}</style>
<style>${katexCss}</style>
</head>
<body>
<main class="print-root markdown-body">${metaHtml}${inner.innerHTML}</main>
<script>
  window.addEventListener('load', function () {
    setTimeout(function () {
      window.focus();
      window.print();
    }, 350);
  });
  window.addEventListener('afterprint', function () { window.close(); });
</script>
</body>
</html>`

  const win = window.open('', '_blank', 'width=920,height=1040')
  if (!win) return false
  win.document.open()
  win.document.write(html)
  win.document.close()
  return true
}
