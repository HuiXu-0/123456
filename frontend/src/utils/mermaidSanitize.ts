/**
 * Mermaid 源码清洗：把「节点形状」内的标签文本规整为可解析形式，
 * 保证含数学公式 / 特殊符号（<、>、∞、sum(w_n^2)、[] 等）的内容也能被 mermaid 解析、不再 Parse error。
 *
 * 做法（只动标签文本，不动图结构）：找到节点形状包裹
 *   A[...]、A(...)、A{...}、A([...])、A[[...]]、A[(...)]、A((...))、A{{...}}
 * 内的标签，统一为「双引号包裹 + 转义危险字符」：
 *   - 引号内允许 () [] {} 数学符号原样出现（mermaid 仅在**无引号**时才把它们当语法字符 → 报错根因）；
 *   - 双引号本身 → #quot;、尖括号 <、> → #60;、#62;（否则被当 HTML 标签 / 语法）；保留 <br/> 换行标签；
 * 首行图类型声明与 subgraph / classDef / style / linkStyle / %% 等结构行保持不动。
 *
 * 清洗后若仍有极端输入解析失败，由 MermaidDiagram 的错误兜底 UI 兜住（不白屏）。
 * 幂等：对「已经是引号包裹的合法标签」再清洗一次结果不变。
 */

/** 形状包裹（开→闭）。按开符号长度从长到短匹配，避免 `[` 抢先吃掉 `[[` / `([`。 */
const WRAPPERS: Array<{ open: string; close: string }> = [
  { open: '([', close: '])' },
  { open: '[[', close: ']]' },
  { open: '[(', close: ')]' },
  { open: '((', close: '))' },
  { open: '{{', close: '}}' },
  { open: '[', close: ']' },
  { open: '(', close: ')' },
  { open: '{', close: '}' },
]

/** 结构 / 声明行：整行原样保留（其内标签本就应由作者写对，且改动易破坏语法）。 */
const STRUCT_RE =
  /^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|quadrantChart|subgraph|end|classDef|class|style|linkStyle|direction|click|%%)\b/

/** 正文不会出现的控制符占位，保护 <br/> 换行标签不被当作尖括号转义。 */
const BR_SENTINEL = String.fromCharCode(1)

/** 节点 id 允许的字符（字母 / 数字 / 下划线 / 连字符 / CJK）。 */
function isIdentChar(ch: string): boolean {
  return /[A-Za-z0-9_一-龥-]/.test(ch)
}

/** 形状闭合符之后应出现的「边界字符」——空白或连线 / 箭头相关字符，用以从内层同类符号中辨认真正的闭合。 */
function isBoundary(ch: string | undefined): boolean {
  return ch === undefined || /[\s\-=.>|&;~:<]/.test(ch)
}

/** 转义标签内文本：去掉外层引号（若有）→ 保护 <br/> → 转义 " < > → 还原 <br/>。 */
function escapeLabel(raw: string): string {
  let s = raw.trim()
  if (s.length >= 2 && s[0] === '"' && s[s.length - 1] === '"') s = s.slice(1, -1)
  s = s.replace(/<br\s*\/?>/gi, BR_SENTINEL)
  s = s.replace(/"/g, '#quot;').replace(/</g, '#60;').replace(/>/g, '#62;')
  s = s.split(BR_SENTINEL).join('<br/>')
  return s
}

function matchOpen(line: string, i: number): { open: string; close: string } | null {
  for (const w of WRAPPERS) if (line.startsWith(w.open, i)) return w
  return null
}

function sanitizeLine(line: string): string {
  if (STRUCT_RE.test(line)) return line
  let out = ''
  let i = 0
  const n = line.length
  while (i < n) {
    const w = i > 0 && isIdentChar(line[i - 1]) ? matchOpen(line, i) : null
    if (w) {
      const contentStart = i + w.open.length
      // 找到「后接边界字符」的最近闭合符——从而跳过标签内部的同类括号（如 sum(w_n^2)）
      let closeAt = -1
      for (let j = contentStart; j <= n - w.close.length; j++) {
        if (line.startsWith(w.close, j) && isBoundary(line[j + w.close.length])) {
          closeAt = j
          break
        }
      }
      if (closeAt >= 0) {
        const inner = line.slice(contentStart, closeAt)
        out += `${w.open}"${escapeLabel(inner)}"${w.close}`
        i = closeAt + w.close.length
        continue
      }
    }
    out += line[i]
    i++
  }
  return out
}

/** 清洗整段 mermaid 源码。空串原样返回。 */
export function sanitizeMermaid(src: string): string {
  if (!src) return src
  return src.split('\n').map(sanitizeLine).join('\n')
}
