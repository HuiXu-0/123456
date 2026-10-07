import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import StudentPortraitPanel from './StudentPortraitPanel'
import { USE_REAL_API } from '../services/api'
import { parseProfile } from '../services/profile'
import {
  ABILITY_RADAR_DIMS,
  selfReportPortrait,
  type PortraitDimension,
  type SelfReportInput,
} from '../services/profileDialogue'
import './DiagnosisEntry.css'

/**
 * 入口②「一段话描述」（任务 2）：用户自然语言描述水平与目标 →
 * 复用 /profile/parse 解析能力 → 映射为同一套 CANONICAL_DIMS 画像（source=self_report，中置信）→
 * 右侧实时画像报告。Mock 兜底：无后端时本地关键词启发式解析，保证「自述 → 能力+偏好画像」可演示。
 */
interface Props {
  onFinish: (dims: PortraitDimension[]) => void
  onBack: () => void
}

const EXAMPLES = [
  '我会 Python，机器学习基础还行，神经网络只懂概念，Transformer 没碰过，想系统学大模型。',
  '计算机本科，深度学习有项目经验，注意力机制不太熟，目标是转 AI 算法岗。',
  '非科班自学半年，机器学习入门了，其余都比较薄弱，想打牢基础。',
]

/** Mock 兜底解析：从描述里按知识点关键词启发式估能力分（响应输入、便于演示「自述→画像」）。 */
function mockSelfParse(text: string): SelfReportInput {
  const t = text.toLowerCase()
  const NEG = ['没碰', '没学', '不会', '不懂', '不熟', '零基础', '没接触', '只懂概念', '薄弱', '陌生']
  const POS = ['熟悉', '会', '掌握', '精通', '擅长', '做过', '有经验', '还行', '入门']
  const ALIAS: Record<string, string[]> = {
    机器学习基础: ['机器学习', 'machine learning', 'ml'],
    神经网络: ['神经网络', 'neural'],
    深度学习: ['深度学习', 'deep learning', 'dl'],
    注意力机制: ['注意力', 'attention'],
    Transformer: ['transformer'],
    大模型微调: ['大模型', '微调', 'finetune', 'fine-tune', 'lora', 'llm'],
  }
  const skills = ABILITY_RADAR_DIMS.map((name) => {
    const keys = ALIAS[name] ?? [name.toLowerCase()]
    let level = 45
    for (const k of keys) {
      const at = t.indexOf(k)
      if (at < 0) continue
      // 取该知识点关键词附近 ±10 字窗口的语气词判定高低（就近匹配，避免全句误判）
      const win = t.slice(Math.max(0, at - 10), at + k.length + 10)
      const neg = NEG.some((w) => win.includes(w.toLowerCase()))
      const pos = POS.some((w) => win.includes(w.toLowerCase()))
      level = neg ? 22 : pos ? 75 : 55
      break
    }
    return { name, level }
  })
  const major = /本科|硕士|博士|研究生|计算机|软件|电子|人工智能|ai/i.test(text) ? '计算机 / AI 相关' : ''
  const goal = /转|求职|岗|工作|面试/.test(text)
    ? '转岗 / 求职'
    : /考|认证|证书/.test(text)
      ? '考试 / 认证'
      : '系统提升能力'
  return { education: major ? '本科及以上' : '', major, goal, skills }
}

export default function SelfReportProfile({ onFinish, onBack }: Props) {
  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [dims, setDims] = useState<PortraitDimension[] | null>(null)

  const runParse = async () => {
    const desc = text.trim()
    if (!desc || parsing) return
    setParsing(true)
    try {
      let input: SelfReportInput
      if (USE_REAL_API) {
        const p = await parseProfile([], desc)
        input = {
          education: p.education.value,
          major: p.major.value,
          goal: p.goal.value,
          skills: p.skills.map((s) => ({ name: s.name, level: s.level })),
        }
      } else {
        input = mockSelfParse(desc)
      }
      setDims(selfReportPortrait(input))
    } catch (e) {
      // 解析失败兜底为本地启发式，保证演示不中断（错误已记录）
      console.error('[self-report] /profile/parse 失败，回退本地解析', e)
      setDims(selfReportPortrait(mockSelfParse(desc)))
    } finally {
      setParsing(false)
    }
  }

  const dimsRecord = useMemo(
    () => (dims ? Object.fromEntries(dims.map((d) => [d.key, d])) : {}),
    [dims]
  )

  return (
    <div className="sr">
      <div className="sr__main profile-builder__card">
        <div className="sr__title">用一段话描述你的情况</div>
        <p className="sr__desc">
          说说你会什么、哪些只懂概念、哪些没碰过，以及你的学习目标。AI 会解析成「能力 + 偏好」画像，
          并如实标注为<strong> 自述 · 中置信 </strong>（自陈推断、非实测，随时可做题升级）。
        </p>
        <textarea
          className="sr__textarea"
          placeholder="例：我会 Python，机器学习基础还行，神经网络只懂概念，Transformer 没碰过，想系统学大模型…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="sr__examples">
          {EXAMPLES.map((ex, i) => (
            <button key={i} className="sr__example" onClick={() => setText(ex)}>
              示例 {i + 1}
            </button>
          ))}
        </div>
        <div className="sr__actions">
          <button className="sr__back" onClick={onBack}>
            ← 换一种方式
          </button>
          {parsing ? (
            <span className="sr__parsing">
              <motion.span animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}>
                ✦
              </motion.span>
              正在解析为画像…
            </span>
          ) : (
            <button className="sr__parse" disabled={!text.trim()} onClick={runParse}>
              {dims ? '重新解析' : '解析为画像 →'}
            </button>
          )}
        </div>
        <p className="sr__hint">
          解析严格基于你的描述，不臆造；一段话通常不涉及学习偏好，故认知风格 / 学习节奏暂标「待校准 · 低置信」，
          可在后续学习中自动校准。
        </p>
      </div>

      <aside>
        {dims ? (
          <StudentPortraitPanel
            dims={dimsRecord}
            updatedAt={dims[0]?.updatedAt}
            complete
            onFinish={() => onFinish(dims)}
          />
        ) : (
          <div className="profile-builder__card sr__empty-hint">
            <p className="sr__desc">解析后，你的「能力 + 偏好 + 主观」画像将在这里实时生成，并标注来源与置信度。</p>
          </div>
        )}
      </aside>
    </div>
  )
}
