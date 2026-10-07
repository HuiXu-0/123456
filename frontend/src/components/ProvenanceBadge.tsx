import { portraitProvenance, type PortraitDimension } from '../services/profileDialogue'

/**
 * 画像来源/置信度徽章（任务 3）：明确标注本画像「有多可信」——
 * 实测·高置信（做题式）/ 自述·中置信（一段话描述）/ 未测·默认零基础（跳过）。
 * 由画像维度的 source/confidence 派生（复用既有字段，不另造一套），画像报告与学情概览共用。
 */
export default function ProvenanceBadge({
  dims,
  showHint = false,
}: {
  dims: PortraitDimension[]
  showHint?: boolean
}) {
  if (!dims.length) return null
  const prov = portraitProvenance(dims)
  return (
    <span className={`prov-badge prov-badge--${prov.tone}`} title={prov.hint}>
      <span className="prov-badge__dot" />
      <span className="prov-badge__label">{prov.label}</span>
      {prov.confidencePct > 0 && <span className="prov-badge__pct">{prov.confidencePct}%</span>}
      {showHint && <span className="prov-badge__hint">· {prov.hint}</span>}
    </span>
  )
}
