/**
 * AI 画像综述（grounding 版）。
 *
 * 设计原则：综述必须严格基于「已抽取的结构化字段 + 原始材料引用」生成，
 * 不得引入字段之外的信息。当前为前端模板合成 —— grounding 是结构性保证：
 * 生成器只能引用 draft 里已有的值，且每个关键结论都携带 source，映射到具体材料。
 *
 * 后端就绪后，仅需把 generateNarrative 的函数体替换为真实 LLM 调用：
 *   - 上下文只喂「各材料抽取文本 + 结构化字段」；
 *   - prompt 强制每个 claim 标注 sourceId、材料未提及则省略；
 *   - 返回结构与本文件一致（paragraphs/sources），UI 与数据契约不变。
 */

export type NarrativeSource = 'resume' | 'ocr' | 'text' | 'manual'

/** 一条可点击回看的信息来源（某份简历 / 某张图 / 文字描述）*/
export interface SourceRef {
  id: string
  label: string
  kind: 'doc' | 'image' | 'text'
}

/** 综述中的一个文本片段；tone 标记高亮，sourceId 链接到来源 chip */
export interface NarrativeSegment {
  text: string
  /** key=关键信息(蓝) / weak=薄弱项(暖色) */
  tone?: 'key' | 'weak'
  /** 该结论对应的来源 id（可点击回看）；manual 估值无来源时省略 */
  sourceId?: string
}

export interface Narrative {
  /** 两段：① 背景/经历/技能 ② 结合目标岗位的能力差距 */
  paragraphs: NarrativeSegment[][]
  sources: SourceRef[]
  materialCount: number
}

interface DraftLike {
  education: { value: string; source: NarrativeSource }
  major: { value: string; source: NarrativeSource }
  goal: { value: string; source: NarrativeSource }
  skills: { name: string; level: number; source: NarrativeSource }[]
}
interface TargetLike {
  name: string
  radar: Record<string, number>
}

/** 来源类别 → 具体材料 id：让"结论⇄出处"可核对 */
function materialIdFor(source: NarrativeSource, materials: SourceRef[]): string | undefined {
  if (source === 'resume') return materials.find((m) => m.kind === 'doc')?.id
  if (source === 'ocr') return materials.find((m) => m.kind === 'image')?.id
  if (source === 'text') return materials.find((m) => m.kind === 'text')?.id
  return undefined // manual：无材料来源
}

/**
 * 生成画像综述。无任何上传材料（手动填写兜底）时返回 null —— 不为凑字数编造经历。
 */
export function generateNarrative(
  draft: DraftLike,
  materials: SourceRef[],
  targetJob: TargetLike | null
): Narrative | null {
  if (materials.length === 0) return null

  const sid = (s: NarrativeSource) => materialIdFor(s, materials)

  /* ===== 第①段：背景 / 经历 / 技能（全部源自结构化字段，逐项挂来源）===== */
  const p1: NarrativeSegment[] = [{ text: '根据已上传材料综合分析，你具备 ' }]
  if (draft.education.value) {
    p1.push({ text: `${draft.education.value}学历`, tone: 'key', sourceId: sid(draft.education.source) })
    p1.push({ text: '，主修 ' })
  }
  if (draft.major.value) {
    p1.push({ text: `${draft.major.value}`, tone: 'key', sourceId: sid(draft.major.source) })
    p1.push({ text: ' 专业' })
  }
  if (draft.goal.value) {
    p1.push({ text: '，学习目标为 ' })
    p1.push({ text: `${draft.goal.value}`, tone: 'key', sourceId: sid(draft.goal.source) })
  }
  p1.push({ text: '。' })

  const strong = [...draft.skills].filter((s) => s.level >= 60).sort((a, b) => b.level - a.level).slice(0, 3)
  if (strong.length) {
    p1.push({ text: '在能力画像上，你的 ' })
    strong.forEach((s, i) => {
      p1.push({ text: s.name, tone: 'key', sourceId: sid(s.source) })
      if (i < strong.length - 1) p1.push({ text: '、' })
    })
    p1.push({ text: ` 基础较为扎实，相关经历在材料中均有体现。` })
  } else {
    p1.push({ text: '材料显示你的各项能力尚处于积累阶段，仍有较大成长空间。' })
  }

  /* ===== 第②段：结合目标岗位的初步能力差距（薄弱项暖色高亮）===== */
  const p2: NarrativeSegment[] = []
  if (targetJob) {
    const gaps = draft.skills.map((s) => ({
      ...s,
      required: targetJob.radar[s.name] ?? 0,
      gap: (targetJob.radar[s.name] ?? 0) - s.level,
    }))
    const met = gaps.filter((g) => g.gap <= 0)
    const weak = gaps.filter((g) => g.gap > 0).sort((a, b) => b.gap - a.gap).slice(0, 2)

    p2.push({ text: '对标 ' })
    p2.push({ text: targetJob.name, tone: 'key' }) // 岗位要求来自实时市场数据，非用户材料，故不挂来源
    p2.push({ text: ' 的实时技能要求，' })

    if (met.length) {
      p2.push({ text: '你在 ' })
      met.slice(0, 2).forEach((g, i) => {
        p2.push({ text: g.name, tone: 'key', sourceId: sid(g.source) })
        if (i < Math.min(met.length, 2) - 1) p2.push({ text: '、' })
      })
      p2.push({ text: ' 等维度已接近或达到要求' })
    } else {
      p2.push({ text: '你当前各维度尚未触及岗位要求线' })
    }

    if (weak.length) {
      p2.push({ text: '；但 ' })
      weak.forEach((g, i) => {
        p2.push({ text: g.name, tone: 'weak', sourceId: sid(g.source) })
        if (i < weak.length - 1) p2.push({ text: '、' })
      })
      p2.push({ text: ' 与岗位要求仍有明显差距，是当前最需补齐的短板。' })
    } else {
      p2.push({ text: '，整体匹配度良好。' })
    }
    p2.push({ text: '建议以此为起点生成针对性学习路径，优先攻克高差距维度。' })
  }

  return {
    paragraphs: targetJob ? [p1, p2] : [p1],
    sources: materials,
    materialCount: materials.length,
  }
}
