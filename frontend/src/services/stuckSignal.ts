/**
 * 即时辅导·卡住信号（B-3）。
 *
 * 用「现有可得的行为数据」累计每个知识点的卡顿权重——测验答错 / 不及格、反复看同一内容等，
 * 达阈值时通知 StuckNudge 主动弹出轻量关怀。判频/去重由订阅方按知识点 + 会话维度负责，
 * 此处只做信号累计与广播，保持职责单一、可被任意行为入口复用。
 */
export const STUCK_THRESHOLD = 2

const counts: Record<string, number> = {}
type Listener = (kpId: string, count: number) => void
const listeners = new Set<Listener>()

/** 上报一次卡住信号：weight 越大代表越强（如不及格记 2，反复浏览记 1）。 */
export function reportStuck(kpId: string, weight = 1): void {
  if (!kpId) return
  counts[kpId] = (counts[kpId] ?? 0) + weight
  listeners.forEach((l) => l(kpId, counts[kpId]))
}

/** 订阅卡住信号（StuckNudge 挂载时）；返回取消订阅函数。 */
export function onStuck(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
