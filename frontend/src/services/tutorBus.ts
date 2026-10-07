/**
 * 即时辅导·打开总线（B-2 / B-3 解耦用）。
 *
 * 让「选中讲义提问」(SelectionAskBubble)、「主动察觉卡顿」(StuckNudge) 等散落入口
 * 无需层层透传 props，即可命令常驻的 AskTutorDock 打开抽屉并预填/自动发起一次提问。
 * 仅一个消费者（页面内唯一的 dock）订阅，故用极轻量的发布订阅，不引入额外状态库。
 */
export interface OpenTutorPayload {
  /** 预填到输入框 / 直接作为问题上下文发起辅导的文本 */
  question?: string
  /** true 时打开抽屉后立即发起该问题（无需用户再点一次） */
  autoAsk?: boolean
}

type Listener = (payload: OpenTutorPayload) => void
const listeners = new Set<Listener>()

/** dock 挂载时订阅；返回取消订阅函数。 */
export function onOpenTutor(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 任意入口调用：打开即时辅导（可携带预填问题 + 是否自动发起）。 */
export function openInstantTutor(payload: OpenTutorPayload = {}): void {
  listeners.forEach((l) => l(payload))
}

/**
 * 全局辅导上下文（B-2 全局化）：常驻的即时辅导 dock 升到 App 顶层后，需要知道「当前在学什么」
 * 才能把提问落到对应知识点/文档。各内容页在进入时用 setTutorContext 声明一次当前上下文，
 * dock 与选中气泡据此发起针对性辅导；未声明时用通用兜底（mock 全链路可跑通）。
 */
export interface TutorContext {
  kpId: string
  kpName: string
}

let currentContext: TutorContext = { kpId: 'general', kpName: '当前内容' }
const ctxListeners = new Set<(c: TutorContext) => void>()

export function getTutorContext(): TutorContext {
  return currentContext
}

/** 内容页声明当前辅导上下文（知识点 / 文档）。同值不重复广播。 */
export function setTutorContext(ctx: TutorContext): void {
  if (ctx.kpId === currentContext.kpId && ctx.kpName === currentContext.kpName) return
  currentContext = ctx
  ctxListeners.forEach((l) => l(ctx))
}

export function onTutorContext(listener: (c: TutorContext) => void): () => void {
  ctxListeners.add(listener)
  return () => {
    ctxListeners.delete(listener)
  }
}

/**
 * 即时辅导·资源生成完成弹窗（问题 5 / 问题 6 改造）。
 *
 * AskTutorDock 内资源生成完毕后，不直接在侧边栏内渲染资源内容，
 * 而是通过此总线通知 GlobalTutorLayer 弹出居中弹窗展示资源。
 */
export interface ResourceGeneratedPayload {
  resources: Array<{
    type: string
    title: string
    // diagram
    mermaid?: string
    // lecture
    markdown?: string
    // example
    statement?: string
    solution?: string
    // video
    scenes?: Array<{ title: string; points: string[]; narration: string }>
    // external
    items?: Array<{
      id: string
      type: '视频' | '论文' | '文档' | '课程'
      title: string
      url: string
      source: string
      duration?: string
      reason: string
    }>
  }>
  kpName: string
  problemPoint: string
}

type ResourceListener = (payload: ResourceGeneratedPayload) => void
const resourceListeners = new Set<ResourceListener>()

/** 监听资源生成完成事件（GlobalTutorLayer 订阅）。 */
export function onResourceGenerated(listener: ResourceListener): () => void {
  resourceListeners.add(listener)
  return () => {
    resourceListeners.delete(listener)
  }
}

/** AskTutorDock 资源生成完毕后调用：打开资源弹窗。 */
export function openResourceModal(payload: ResourceGeneratedPayload): void {
  resourceListeners.forEach((l) => l(payload))
}
