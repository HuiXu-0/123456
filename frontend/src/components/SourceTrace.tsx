import { sourcePercent } from '../utils/citations'
import './SourceTrace.css'

export interface SourceRef {
  title: string
  type: '教材' | '论文' | '文档' | '课程'
  confidence: number
}

/* 模拟 RAG 检索 + 审核 Agent 给出的溯源来源（体现防幻觉/可信）；
   导出供资源页状态条「RAG 引用数」在 mock 模式取真实条数（与讲义溯源实际列出的来源一致）。*/
export const defaultSources: SourceRef[] = [
  { title: '《深度学习》 Goodfellow, Bengio, Courville', type: '教材', confidence: 98 },
  { title: 'arXiv:1706.03762 — Attention Is All You Need', type: '论文', confidence: 95 },
  { title: 'CS231n: Neural Networks（Stanford）', type: '课程', confidence: 94 },
  { title: 'PyTorch 官方文档 · nn.Module', type: '文档', confidence: 92 },
  { title: '《神经网络与深度学习》 邱锡鹏', type: '教材', confidence: 90 },
]

/**
 * RAG 溯源 · 论文级参考文献样式。
 *
 * 从旧版「可折叠大框」升级为学术引用列表：编号 [n]、标题 + 出处（教材/论文/课程/文档）+
 * 可信度，小字悬挂缩进，整体像论文末尾的参考文献。用于无正文正文可挂载上标的卡片场景
 * （资源库预览、文档学习各资源卡）；讲义正文的上标引用 [1][2] 由 MarkdownRenderer 就地织入。
 */
export default function SourceTrace({ sources = defaultSources }: { sources?: SourceRef[] }) {
  if (!sources.length) return null
  return (
    <section className="paper-refs" aria-label="RAG 溯源参考文献">
      <h4 className="paper-refs__title">
        <span className="paper-refs__badge">🛡 RAG 溯源</span>
        参考文献
        <span className="paper-refs__count">多智能体交叉校验 · {sources.length} 篇</span>
      </h4>
      <ol className="paper-refs__list">
        {sources.map((s, i) => (
          <li key={`${s.title}-${i}`} className="paper-refs__item">
            <span className="paper-refs__name">{s.title}</span>
            <span className="paper-refs__meta">
              · {s.type} · 可信度 {sourcePercent(s.confidence)}%
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}
