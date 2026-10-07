/**
 * 资源「逐项生成」状态原语（会话二 TutorResourcePanel 与 主资源生成 LearningResource 共用）。
 *
 * 抽出一套：状态枚举 + 文案/样式映射 + 状态标签组件，两处共享，保证「智能辅导面板」与
 * 「学习资源页·资源中枢」的逐项进度/状态体验完全一致（不另造一套）。
 * 样式类沿用 TutorResourcePanel.css 的 .trp__chip（两处都导入该 CSS）。
 */

import { useEffect, useRef, useState } from 'react'

export type ItemStatus = 'pending' | 'running' | 'done' | 'error'

export const STATUS_META: Record<ItemStatus, { label: string; cls: string }> = {
  pending: { label: '待生成', cls: 'is-pending' },
  running: { label: '生成中…', cls: 'is-running' },
  done: { label: '已完成', cls: 'is-done' },
  error: { label: '失败', cls: 'is-error' },
}

export function StatusChip({ status }: { status: ItemStatus }) {
  const m = STATUS_META[status]
  return <span className={`trp__chip ${m.cls}`}>{m.label}</span>
}

/* ==================== 客户端阶段化生成进度（文档学习 与 学习资源页 共用） ====================
   自文档学习页抽出的「生成中 · N% · 当前阶段 + 进度条」推进机制：向 92% 缓动逼近
   （留 8% 待真正返回时补满），成功补满 100% 后淡出清除，失败直接清除。两页同一套，不另造。 */

export type GenProgress = { pct: number; stage: string }

/** 普通形态 4 段阶段文案；视频（长任务）5 段更慢推进，给出「进行到哪一步」的明确反馈。 */
export const GEN_STAGES = ['检索文档片段', '组织知识结构', '生成内容', '排版与渲染'] as const
export const VIDEO_STAGES = ['检索文档片段', '编写分镜脚本', '生成同步旁白', '合成视频画面', '渲染输出'] as const

/** 按 key 管理多个并行任务的阶段化进度；卸载自动清理全部定时器。 */
export function useStagedProgress<K extends string>() {
  const [progress, setProgress] = useState<Partial<Record<K, GenProgress>>>({})
  const timers = useRef<Partial<Record<K, number>>>({})

  /** 启动某 key 的阶段化进度；slow=长任务（视频）推进更慢。 */
  const startProgress = (key: K, stages: readonly string[], opts?: { slow?: boolean }) => {
    setProgress((prev) => {
      const next = { ...prev }
      next[key] = { pct: 6, stage: stages[0] }
      return next
    })
    const existing = timers.current[key]
    if (existing) window.clearInterval(existing)
    timers.current[key] = window.setInterval(() => {
      setProgress((prev) => {
        const cur = prev[key]?.pct ?? 6
        const ease = opts?.slow ? 0.08 : 0.16
        const pct = Math.min(92, cur + Math.max(1.4, (92 - cur) * ease))
        const si = Math.min(stages.length - 1, Math.floor((pct / 92) * stages.length))
        const next = { ...prev }
        next[key] = { pct, stage: stages[si] }
        return next
      })
    }, opts?.slow ? 620 : 430)
  }

  /** 结束进度：成功先补满 100% 再淡出清除；失败直接清除。 */
  const stopProgress = (key: K, ok: boolean) => {
    const t = timers.current[key]
    if (t) {
      window.clearInterval(t)
      delete timers.current[key]
    }
    if (ok) {
      setProgress((prev) => {
        const next = { ...prev }
        next[key] = { pct: 100, stage: '完成' }
        return next
      })
      window.setTimeout(
        () =>
          setProgress((prev) => {
            const next = { ...prev }
            delete next[key]
            return next
          }),
        500
      )
    } else {
      setProgress((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }

  /* 卸载清理所有进度定时器 */
  useEffect(
    () => () => {
      Object.values(timers.current).forEach((t) => t && window.clearInterval(t as number))
    },
    []
  )

  return { progress, setProgress, startProgress, stopProgress }
}
