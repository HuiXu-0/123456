import { Suspense, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkMath from 'remark-math'
import remarkGfm from 'remark-gfm'
import rehypeKatex from 'rehype-katex'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import 'katex/dist/katex.min.css'
import type { SourceRef } from './SourceTrace'
import { weaveCitations } from '../utils/citations'
import InlineErrorBoundary, { lazySafe } from './InlineErrorBoundary'
import './MarkdownRenderer.css'

// 讲义内嵌图解：```mermaid 围栏复用既有 MermaidDiagram 渲染（懒加载，避免无图解页引入 mermaid；
// lazySafe：chunk 拉取失败只降级该图，不上抛打崩整个视图）。
const MermaidDiagram = lazySafe(() => import('./MermaidDiagram'), '知识图解')

/**
 * 数学公式定界符归一化：把 LaTeX 原生定界符统一成 remark-math 认得的 `$`/`$$`。
 *
 * 根因：真实 / 缓存讲义（如后端知识原子、DeepSeek 产出）常用 `\( … \)`、`\[ … \]`
 * 定界数学，而 remark-math 只解析 `$ … $` 与 `$$ … $$` → 未归一化时公式以裸 LaTeX
 * 源码（`\sigma`、`\frac`…）显示。本函数在进 react-markdown 前做一次幂等转换：
 *   - 行内 `\( … \)` → `$ … $`；
 *   - 块级 `\[ … \]`（可跨行，含 `\begin{}…\end{}`）→ 独立成段的 `$$ … $$`。
 * 代码围栏 ```…``` 与行内 `code` 段整体跳过（其中的反斜杠是字面量，不能改写）。
 * 内容本就用 `$`/`$$`（无 `\(`、`\[`）时提前返回，零改动、不影响既有讲义。
 */
function normalizeMathDelimiters(src: string): string {
  if (!src || (!src.includes('\\(') && !src.includes('\\['))) return src
  // 按代码块 / 行内代码切分：奇数段为代码（原样保留），偶数段做定界符转换。
  return src
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((seg, i) => {
      if (i % 2 === 1) return seg
      return seg
        .replace(/\\\[([\s\S]+?)\\\]/g, (_m, inner) => `\n\n$$${String(inner).trim()}$$\n\n`)
        .replace(/\\\(([\s\S]+?)\\\)/g, (_m, inner) => `$${String(inner).trim()}$`)
    })
    .join('')
}

/**
 * 去除 HTML 注释（如后端产出的 `<!-- media:enriched -->` 埋点标记）——react-markdown 未接
 * rehype-raw，这类注释会被当字面文本显示。代码围栏 ```…``` 与行内 `code` 内的 `<!-- -->`
 * 是字面量，跳过不动；内容无 `<!--` 时提前返回，零改动。
 */
function stripHtmlComments(src: string): string {
  if (!src || !src.includes('<!--')) return src
  return src
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((seg, i) => (i % 2 === 1 ? seg : seg.replace(/<!--[\s\S]*?-->/g, '')))
    .join('')
}

/**
 * 讲义配图：图片渲染 + 来源标注（来源走紧随其后的 Markdown 段落）+ 裂图兜底。
 * 加载失败时优雅隐藏破图、显示占位（不显示浏览器默认破图标）。
 */
function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) {
    return (
      <span className="md-image-fallback" role="img" aria-label={alt || '图片暂不可用'}>
        🖼️ 图片暂不可用
      </span>
    )
  }
  return (
    <img
      className="md-image"
      src={src}
      alt={alt || ''}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}

/**
 * Markdown 讲义渲染（react-markdown v9 + Prism 代码高亮 + KaTeX 数学公式）。
 * - remark-math 解析行内 $...$ 与块级 $$...$$，rehype-katex 渲染为公式；
 * - remark-gfm 解析 GFM 扩展（表格 | --- |、删除线 ~~、任务列表 - [ ]、自动链接）；
 * - ```mermaid 围栏走 MermaidDiagram（讲义内嵌结构 / 流程图解）；
 * - 图片走 MarkdownImage（来源标注 + 裂图兜底）；urlTransform 放行自包含 data:image 占位图。
 *
 * 论文级 RAG 溯源（可选 `sources`）：传入来源后，正文相应段落挂上 [1][2] 上标引用（复用
 * remark-gfm 原生脚注），末尾自动汇聚成学术格式「参考文献」区块——像论文而非大框。
 */
export default function MarkdownRenderer({
  content,
  sources,
}: {
  content: string
  sources?: SourceRef[]
}) {
  const base = stripHtmlComments(content)
  const withCites = sources?.length ? weaveCitations(base, sources).content : base
  const normalized = normalizeMathDelimiters(withCites)
  return (
    <div className="markdown-body">
      {/* 解析/渲染级兜底：万一 markdown 管线本身抛错，降级为可读的原文纯文本，不打崩视图 */}
      <InlineErrorBoundary
        label="讲义正文"
        fallback={<pre className="md-raw-fallback">{content}</pre>}
      >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={(url) => (url.startsWith('data:image/') ? url : defaultUrlTransform(url))}
        components={{
          code({ node: _node, className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '')
            if (match && match[1] === 'mermaid') {
              return (
                <InlineErrorBoundary label="知识图解">
                  <Suspense fallback={<div className="mermaid-loading">图解加载中…</div>}>
                    <MermaidDiagram chart={String(children).replace(/\n$/, '')} />
                  </Suspense>
                </InlineErrorBoundary>
              )
            }
            return match ? (
              <SyntaxHighlighter
                style={oneDark}
                language={match[1]}
                PreTag="div"
                customStyle={{ borderRadius: '12px', margin: '16px 0', fontSize: '13px' }}
              >
                {String(children).replace(/\n$/, '')}
              </SyntaxHighlighter>
            ) : (
              <code className="md-inline-code" {...props}>
                {children}
              </code>
            )
          },
          pre: ({ children }) => <div className="md-codeblock">{children}</div>,
          img: ({ src, alt }) => <MarkdownImage src={typeof src === 'string' ? src : undefined} alt={alt} />,
          h1: ({ children }) => <h1 className="resource-title">{children}</h1>,
          // remark-gfm 脚注区默认标题是英文「Footnotes」→ 归一化成学术「参考文献」。
          h2: ({ children, id }) =>
            id === 'footnote-label' ? (
              <h2 className="paper-refs__title">参考文献</h2>
            ) : (
              <h2 className="section-title">{children}</h2>
            ),
          h3: ({ children }) => <h3 className="subsection-title">{children}</h3>,
          p: ({ children }) => <p className="content-paragraph">{children}</p>,
          ul: ({ children }) => <ul className="content-list">{children}</ul>,
          ol: ({ children }) => <ol className="ordered-list">{children}</ol>,
          blockquote: ({ children }) => <blockquote className="content-quote">{children}</blockquote>,
          // GFM 表格：包一层可横向滚动容器，宽表（如多列对比表）不撑破讲义布局。
          table: ({ children }) => (
            <div className="md-table-wrap">
              <table className="md-table">{children}</table>
            </div>
          ),
        }}
      >
        {normalized}
      </ReactMarkdown>
      </InlineErrorBoundary>
    </div>
  )
}
