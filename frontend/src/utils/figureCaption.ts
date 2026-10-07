/**
 * 论文级图注工具（纯前端展示层，不触碰图解/导图的生成逻辑与数据）。
 *
 * - 统一字体栈：拉丁/数字 → Times New Roman，中文 → 衬线体（宋体系兜底），与
 *   globals.css 的 `--font-paper` 保持一致。导出的独立 SVG 无 :root 变量，需用此字面量。
 * - 全局图号：图解与思维导图共用一套序号，按出现顺序编号（图 1、图 2 …），呈现学术图注观感。
 * - 图注文案：基于当前知识点/主题词生成，如「图 1　神经网络前向传播 · 知识脉络图解」。
 */

/** 论文字体栈字面量（与 globals.css `--font-paper` 同源；导出场景不能依赖 CSS 变量）。 */
export const PAPER_FONT_STACK =
  "'Times New Roman', 'Source Han Serif SC', 'Source Han Serif CN', 'Songti SC', 'STSong', 'SimSun', 'Noto Serif SC', serif"

let figureSeq = 0

/** 领取下一个全局图号（图解 / 导图共用，按挂载出现顺序递增）。 */
export function nextFigureNumber(): number {
  return ++figureSeq
}

/** 从 downloadName（如「图解-神经网络」「思维导图-XXX」）还原主题词，供图注使用。 */
export function deriveTopic(downloadName?: string): string {
  if (!downloadName) return ''
  return downloadName.replace(/^(知识图解|图解|思维导图|导图)[-·：:\s]*/, '').trim()
}

/** 组装学术风格图注：`图 N　主题 · 类型`（主题为空时仅显示类型）。 */
export function buildFigureCaption(kind: 'diagram' | 'mindmap', topic: string, n: number): string {
  const label = kind === 'diagram' ? '知识脉络图解' : '结构化思维导图'
  const body = topic ? `${topic} · ${label}` : label
  return `图 ${n}　${body}`
}
