import { useEffect, useMemo, useRef, useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { downloadCodeFile } from '../utils/resourceExport'
import './CodeSandbox.css'

/**
 * 本地代码沙箱：左侧编辑 index.js，右侧 iframe srcdoc 即时运行——零外部依赖。
 *
 * 背景：此前用 @codesandbox/sandpack-react（static 模板），预览依赖 codesandbox.io
 * 远端服务且其注入的压缩中继脚本曾与 demo 顶层变量撞名（`var x`）导致预览静默空白；
 * 现改为把 HTML+JS 直接注入 <iframe srcdoc>（sandbox="allow-scripts"），断网可用、
 * 运行环境干净。代码内容由调用方按当前知识点传入（见 data/kpResources.ts 的 kpCodeDemo）。
 */

/** 下载用 index.html 模板：下载的 index.html + index.js 两个文件可直接本地双击运行。 */
const HTML_TEMPLATE = `<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body><div id="app"></div><script src="index.js"></script></body>
</html>`

/** 编辑器双层（高亮层 + 透明 textarea）必须完全一致的字体度量。 */
const MONO_FONT = 'Consolas, "Courier New", monospace'
const CODE_FONT_SIZE = 13
const CODE_LINE_HEIGHT = 1.6
const CODE_PADDING = '14px 16px'

/** 当前平台主题（鼠尾草默认 / 墨纸 ink，均为浅色纸感），跟随 <html data-theme> 实时切换。
 *  编辑器高亮统一用 oneLight（两主题都是浅底）；主题值仅用于触发预览 srcdoc 重取主题令牌。 */
function usePlatformTheme(): 'sage' | 'ink' {
  const read = () => (document.documentElement.getAttribute('data-theme') === 'ink' ? 'ink' : 'sage')
  const [theme, setTheme] = useState<'sage' | 'ink'>(read)
  useEffect(() => {
    const mo = new MutationObserver(() => setTheme(read()))
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => mo.disconnect()
  }, [])
  return theme
}

/** 组装预览文档：平台主题底色 + 错误提示条 + 用户代码（经典 script，srcdoc 全新文档无命名污染）。 */
function buildSrcdoc(js: string): string {
  const cs = getComputedStyle(document.documentElement)
  const bg = cs.getPropertyValue('--surface').trim() || '#FAF9F4'
  const fg = cs.getPropertyValue('--ink').trim() || '#262A23'
  // srcdoc 内嵌脚本以 </script 结束标签截断，需转义；srcdoc 是全新文档，无重复声明累积问题
  const safeJs = js.replace(/<\/script/gi, '<\\/script')
  const prefix = `<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
  body { margin: 0; padding: 16px; font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 14px; line-height: 1.8; background: ${bg}; color: ${fg}; }
  #__sandbox_error { display: none; margin: 0 0 12px; padding: 8px 12px; border-radius: 8px; background: rgba(220, 38, 38, 0.12); color: #dc2626; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
</style></head>
<body>
<div id="__sandbox_error"></div>
<div id="app"></div>
<script>
var __userCodeLineOffset;
window.addEventListener('error', function (e) {
  var el = document.getElementById('__sandbox_error');
  el.style.display = 'block';
  var line = e.lineno && __userCodeLineOffset ? e.lineno - __userCodeLineOffset : 0;
  el.textContent = '⚠ 代码有错误：' + (e.message || String(e.error)) + (line > 0 ? '（约第 ' + line + ' 行）' : '');
});
</script>
<script>
`
  // 让报错行号对齐到编辑器里的行：用户代码之前的文档行数
  const offset = prefix.split('\n').length - 1
  return prefix.replace('var __userCodeLineOffset;', `var __userCodeLineOffset = ${offset};`) + safeJs + `
</script>
</body></html>`
}

/** 高亮层 + 透明 textarea 叠加的轻量代码编辑器（无第三方编辑器依赖）。 */
function CodeEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const hlRef = useRef<HTMLDivElement>(null)

  const syncScroll = (ta: HTMLTextAreaElement) => {
    const hl = hlRef.current
    if (hl) {
      hl.scrollTop = ta.scrollTop
      hl.scrollLeft = ta.scrollLeft
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Tab') return
    e.preventDefault()
    const ta = e.currentTarget
    const { selectionStart: start, selectionEnd: end } = ta
    onChange(value.slice(0, start) + '  ' + value.slice(end))
    requestAnimationFrame(() => ta.setSelectionRange(start + 2, start + 2))
  }

  const layerTextStyle = { fontFamily: MONO_FONT, fontSize: CODE_FONT_SIZE, lineHeight: CODE_LINE_HEIGHT, whiteSpace: 'pre' as const }
  return (
    <div className="csx-editor">
      <div className="csx-editor__hl" ref={hlRef} aria-hidden="true">
        <SyntaxHighlighter
          language="javascript"
          style={oneLight}
          customStyle={{ margin: 0, padding: CODE_PADDING, background: 'transparent', overflow: 'visible', ...layerTextStyle }}
          codeTagProps={{ style: { ...layerTextStyle, background: 'transparent' } }}
        >
          {value + '\n'}
        </SyntaxHighlighter>
      </div>
      <textarea
        className="csx-editor__input"
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          syncScroll(e.target)
        }}
        onScroll={(e) => syncScroll(e.currentTarget)}
        onKeyDown={onKeyDown}
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        autoCorrect="off"
        wrap="off"
        aria-label="代码编辑器（index.js）"
        style={{ ...layerTextStyle, padding: CODE_PADDING }}
      />
    </div>
  )
}

/**
 * 可运行代码沙箱：学习者直接跑/改当前知识点的代码 demo。
 * - js：index.js 初始内容（按知识点传入，勿再写死）；
 * - baseName：下载文件名前缀（如「代码-知识点」→「代码-知识点-index.js」）；下载取当前编辑内容。
 */
export default function CodeSandbox({ js, baseName = '代码实操' }: { js: string; baseName?: string }) {
  const theme = usePlatformTheme()
  const [code, setCode] = useState(js)
  const [activeTab, setActiveTab] = useState<'js' | 'html'>('js')
  // 运行中的文档：编辑后防抖 500ms 自动重跑；「重新运行」立即重跑（runId 强制重建 iframe）
  const [running, setRunning] = useState(() => ({ doc: buildSrcdoc(js), runId: 0 }))

  useEffect(() => setCode(js), [js]) // 知识点切换 → 重置为该点 demo

  useEffect(() => {
    const timer = setTimeout(() => {
      // 文档没变（如挂载首帧、主题未影响令牌）则不重建 iframe，避免 demo 无谓重跑
      setRunning((r) => {
        const doc = buildSrcdoc(code)
        return doc === r.doc ? r : { doc, runId: r.runId + 1 }
      })
    }, 500)
    return () => clearTimeout(timer)
  }, [code, theme])

  const files = useMemo(
    () => [
      { name: 'index.js', content: code },
      { name: 'index.html', content: HTML_TEMPLATE },
    ],
    [code]
  )

  return (
    <div className="resource-export-block csx">
      <div className="csx__bar">
        <span className="csx__badge">本地沙箱</span>
        <span className="csx__note">左侧改代码，右侧即时运行 · 无需联网</span>
        <button
          type="button"
          className="lecture-export__btn"
          onClick={() => setRunning((r) => ({ doc: buildSrcdoc(code), runId: r.runId + 1 }))}
          title="立即重新运行当前代码"
        >
          <span aria-hidden="true">▶</span> 重新运行
        </button>
        <div className="lecture-export csx__downloads">
          <span className="lecture-export__label">下载代码：</span>
          {files.map((f) => (
            <button
              key={f.name}
              type="button"
              className="lecture-export__btn"
              onClick={() => downloadCodeFile(f.content, `${baseName}-${f.name}`)}
              title={`下载 ${f.name}（当前沙箱内容，${f.content.length} 字符）`}
            >
              <span aria-hidden="true">⬇</span> {f.name}
            </button>
          ))}
        </div>
      </div>
      <div className="csx__tabs" role="tablist">
        <button type="button" role="tab" aria-selected={activeTab === 'js'} className={`csx__tab ${activeTab === 'js' ? 'csx__tab--active' : ''}`} onClick={() => setActiveTab('js')}>
          index.js
        </button>
        <button type="button" role="tab" aria-selected={activeTab === 'html'} className={`csx__tab ${activeTab === 'html' ? 'csx__tab--active' : ''}`} onClick={() => setActiveTab('html')}>
          index.html <span className="csx__tab-ro">只读模板</span>
        </button>
      </div>
      <div className="csx__panes">
        <div className="csx__editor-pane">
          {activeTab === 'js' ? (
            <CodeEditor value={code} onChange={setCode} />
          ) : (
            <SyntaxHighlighter
              language="markup"
              style={oneLight}
              customStyle={{ margin: 0, padding: CODE_PADDING, background: 'transparent', fontFamily: MONO_FONT, fontSize: CODE_FONT_SIZE, lineHeight: CODE_LINE_HEIGHT }}
            >
              {HTML_TEMPLATE}
            </SyntaxHighlighter>
          )}
        </div>
        <iframe key={running.runId} className="csx__preview" sandbox="allow-scripts" srcDoc={running.doc} title="代码运行预览" />
      </div>
    </div>
  )
}
