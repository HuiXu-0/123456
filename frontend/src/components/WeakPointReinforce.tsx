import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { QuizQuestion } from './QuizRenderer'
import QuizRenderer from './QuizRenderer'
import { USE_REAL_API } from '../services/api'
import { reinforce } from '../services/resource'
import { getResourceKpId } from '../services/resourceNav'

type ReinforceCard = { point: string; recap: string; practice: QuizQuestion }

/* 精修强化卡（错题 id → 强化讲解 + 针对性变式练习）：目前覆盖 nn 种子题库前三题；
   未命中的错题走 derivedCard 按题目本身生成（错因回顾 + 原题重练），任意知识点/题库均可用。 */
const reinforcementBank: Record<string, ReinforceCard> = {
  nn_q1: {
    point: '神经元运算顺序',
    recap: '记忆口诀：**先乘后加再激活** —— ① 输入×权重求和 → ② 加偏置 b → ③ 激活函数。顺序不能颠倒，因为激活必须作用在「加权和+偏置」的结果上。',
    practice: {
      question_id: 'r-q1',
      question_type: 'single',
      question_text: '【强化】若把激活函数放到加权求和之前，会发生什么？',
      options: [
        { option_id: 'a', option_text: '结果不变，顺序无所谓' },
        { option_id: 'b', option_text: '失去对「加权和」整体的非线性变换，等价于线性模型' },
        { option_id: 'c', option_text: '会让网络收敛更快' },
      ],
      correct_answer: 'b',
      explanation: '激活必须作用于加权和+偏置的结果，提前激活会破坏非线性表达能力。',
    },
  },
  nn_q2: {
    point: '激活函数辨析',
    recap: '激活函数 = 给神经元引入**非线性**的函数。常见三个：**ReLU**（max(0,x)）、**Sigmoid**、**Tanh**。注意「梯度 Gradient」是反向传播里的概念，**不是**激活函数。',
    practice: {
      question_id: 'r-q2',
      question_type: 'multiple',
      question_text: '【强化】下列关于激活函数，正确的有？（多选）',
      options: [
        { option_id: 'a', option_text: 'ReLU 在正区间梯度恒为 1' },
        { option_id: 'b', option_text: 'Sigmoid 输出范围是 (0,1)' },
        { option_id: 'c', option_text: 'Gradient 是一种激活函数' },
        { option_id: 'd', option_text: 'Tanh 输出零均值，范围 (-1,1)' },
      ],
      correct_answer: ['a', 'b', 'd'],
      explanation: 'ReLU/Sigmoid/Tanh 描述均正确；Gradient（梯度）不是激活函数。',
    },
  },
  nn_q3: {
    point: 'ReLU 与梯度消失',
    recap: '**ReLU** 在正区间导数恒为 1，反向传播时梯度不会被反复压缩，因此能**缓解梯度消失**；而 Sigmoid/Tanh 在饱和区导数趋近 0，深层网络易梯度消失。',
    practice: {
      question_id: 'r-q3',
      question_type: 'boolean',
      question_text: '【强化】Sigmoid 在深层网络中比 ReLU 更容易引起梯度消失。',
      options: [
        { option_id: 'true', option_text: '正确' },
        { option_id: 'false', option_text: '错误' },
      ],
      correct_answer: 'true',
      explanation: 'Sigmoid 两端饱和、导数趋零，深层叠加后梯度迅速衰减，比 ReLU 更易梯度消失。',
    },
  },
}

export default function WeakPointReinforce({ wrong }: { wrong: QuizQuestion[] }) {
  const [generating, setGenerating] = useState(true)

  /* 联调数据源：POST /reinforce（接口 25）上报错题 id，渲染后端真实 recap + 针对性练习；
     mock 模式不请求，沿用本地 reinforcementBank */
  const [remoteItems, setRemoteItems] = useState<
    { point: string; recap: string; practice: QuizQuestion }[] | null
  >(null)
  const localItems = wrong.map((w) => reinforcementBank[w.question_id] ?? derivedCard(w))
  const items = USE_REAL_API && remoteItems ? remoteItems : localItems

  useEffect(() => {
    setGenerating(true)
    if (USE_REAL_API) {
      let alive = true
      reinforce(getResourceKpId(), wrong.map((w) => w.question_id))
        .then((cards) => {
          if (!alive) return
          setRemoteItems(cards.map((c) => ({ point: c.point, recap: c.recap, practice: c.practice })))
          setGenerating(false)
        })
        .catch((e) => {
          console.error('[reinforce] 错题强化生成失败', e) // 失败回退本地题库兜底
          if (!alive) return
          setRemoteItems(null)
          setGenerating(false)
        })
      return () => {
        alive = false
      }
    }
    const t = window.setTimeout(() => setGenerating(false), 1400)
    return () => window.clearTimeout(t)
  }, [wrong])

  if (wrong.length === 0) return null

  return (
    <motion.div
      className="weak"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="weak__head">
        <span className="weak__icon">🎯</span>
        <div>
          <div className="weak__title">错题驱动 · 针对性强化</div>
          <div className="weak__sub">
            诊断 Agent 检测到 {wrong.length} 个薄弱点，领域知识生成 Agent 已为你定制强化练习
          </div>
        </div>
      </div>

      <AnimatePresence mode="wait">
        {generating ? (
          <motion.div key="gen" className="weak__gen" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="weak__gen-orb" />
            <span>AI 正在针对你的薄弱点重新生成强化练习…</span>
            <div className="weak__gen-tags">
              {items.map((it) => <span key={it.point} className="weak__gen-tag">{it.point}</span>)}
            </div>
          </motion.div>
        ) : (
          <motion.div key="content" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            {items.map((it, i) => (
              <div key={it.point} className="weak__card">
                <div className="weak__card-head">
                  <span className="weak__badge">薄弱点 {i + 1}</span>
                  <span className="weak__point">{it.point}</span>
                </div>
                <div className="weak__recap" dangerouslySetInnerHTML={{ __html: mdBold(it.recap) }} />
                <div className="weak__practice">
                  <QuizRenderer questions={[it.practice]} />
                </div>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

/* 极简 **加粗** 渲染（仅用于 recap 短文本）*/
function mdBold(s: string) {
  return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

/* 未收录精修卡的错题：按题目本身生成强化卡——考点取题干、讲解取解析、练习为原题重练。
   不依赖任何知识点写死内容，任意题库（78 点核心/非核心、联调兜底）均适用。 */
function derivedCard(w: QuizQuestion): ReinforceCard {
  const point = w.question_text.replace(/^【[^】]*】/, '').replace(/[？?。！!].*$/, '').slice(0, 18)
  return {
    point,
    recap: `这题你上轮答错了。**解析**：${w.explanation || '回到讲义中对应小节，先弄懂考点再重练。'}`,
    practice: {
      ...w,
      question_id: `r-${w.question_id}`,
      question_text: `【强化重练】${w.question_text}`,
    },
  }
}
