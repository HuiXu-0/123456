import type { SourceRef } from '../components/SourceTrace'

/** confidence 兼容 0-1 与 0-100 两种口径 → 统一为整数百分比。 */
export function sourcePercent(confidence: number): number {
  return confidence <= 1 ? Math.round(confidence * 100) : Math.round(confidence)
}

/** 单条来源 → 学术引用格式的一行（脚注定义体，允许行内 markdown 加粗）。 */
function formatSourceEntry(s: SourceRef): string {
  return `**${s.title}** · ${s.type} · 可信度 ${sourcePercent(s.confidence)}%`
}

/**
 * 论文级引用编织：把 RAG 溯源来源以「正文上标 [1][2] + 末尾参考文献」的方式织入讲义 markdown。
 *
 * 复用 remark-gfm 原生脚注（`[^id]` 引用 + `[^id]:` 定义）——react-markdown 会把引用渲染成
 * `<sup><a>` 上标、把定义汇聚成末尾 `.footnotes` 区块（天然的参考文献列表），语义正确、
 * 无需注入裸 HTML。返回 `wove=false` 时（无正文段落可挂载）调用方回退到独立来源清单。
 *
 * - 代码围栏 ```…``` 视为原子块，绝不在其中插入标记；
 * - 仅把标记挂到「正文段落」块（排除标题 / 列表 / 引用 / 表格 / 独立公式 / 代码）；
 * - 逐条来源轮转分配到可用正文段落，保证每条来源至少被引用一次（未被引用的脚注不会渲染）。
 */
export function weaveCitations(
  markdown: string,
  sources: SourceRef[]
): { content: string; wove: boolean } {
  if (!markdown || !sources.length) return { content: markdown, wove: false }
  // 已含脚注定义的内容不再二次编织，避免重复标记。
  if (/^\s*\[\^[^\]]+\]:/m.test(markdown)) return { content: markdown, wove: false }

  // —— 围栏感知的分块：空行分段，但 ```fence``` 区间整体保留为一块。——
  const lines = markdown.split('\n')
  const blocks: string[] = []
  let cur: string[] = []
  let inFence = false
  const flush = () => {
    if (cur.length) {
      blocks.push(cur.join('\n'))
      cur = []
    }
  }
  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      inFence = !inFence
      cur.push(line)
      continue
    }
    if (!inFence && line.trim() === '') {
      flush()
      continue
    }
    cur.push(line)
  }
  flush()

  // 正文段落：非标题 / 引用 / 表格 / 列表 / 代码 / 独立公式，且非空。
  const isProse = (b: string): boolean => {
    const t = b.trimStart()
    if (!t) return false
    return !/^(#|>|\||```|[-*+]\s|\d+\.\s|\$\$)/.test(t)
  }
  const proseIdx = blocks.map((b, i) => (isProse(b) ? i : -1)).filter((i) => i >= 0)
  if (!proseIdx.length) return { content: markdown, wove: false }

  // 轮转分配：第 k 条来源挂到第 (k % 段落数) 个正文段落尾部，保证每条都被引用。
  const marks: Record<number, number[]> = {}
  sources.forEach((_, k) => {
    const bi = proseIdx[k % proseIdx.length]
    ;(marks[bi] ??= []).push(k + 1)
  })

  const woven = blocks
    .map((b, i) => (marks[i] ? b + marks[i].map((n) => `[^ref${n}]`).join('') : b))
    .join('\n\n')

  const defs = sources.map((s, k) => `[^ref${k + 1}]: ${formatSourceEntry(s)}`).join('\n')

  return { content: `${woven}\n\n${defs}`, wove: true }
}
