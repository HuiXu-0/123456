/**
 * 资源页路由传参通道。
 *
 * App 是 stage 状态机（无 URL 路由参数），页面经 AnimatePresence 按 key 重挂载：
 * 导航前由来源页写入参数，资源页挂载时读取——等价于路由 query，不触碰路由结构。
 *
 * - kpId：粘性。资源页及其子组件（外部资源 / 错题强化）在整个停留期间持续读取；
 *   未经学习路径页进入（如侧边栏直达）时保持上次值，初始为 CURRENT_KP_ID（nn），
 *   与修复前行为一致。
 * - entryTab：一次性（读后即清）。避免侧边栏再次进入资源页时残留落点。
 */
import { CURRENT_KP_ID, kpById } from '../data/knowledgePoints'
import { ksPointById } from '../data/knowledgeSystem'

/** 资源页进入模式：flow=费曼+康奈尔有序学习流；browse=8-tab 资源中枢自由浏览。 */
export type ResourceMode = 'flow' | 'browse'

let kpId: string = CURRENT_KP_ID
let entryTab: string | null = null
/** 进入意图：一次性。null=无显式意图（侧边栏 / 知识图谱直达）→ 资源页落「资源总览 hub」。 */
let entryMode: ResourceMode | null = null

/**
 * 导航去资源页前调用：指定目标知识点 +（可选）进入意图 +（可选）落点 Tab。未知 kpId 回退默认。
 * kpId 合法域 = 78 点全体系（6 核心 + 72 体系目录点，后端生成端点对任意在库 kp 可用，
 * 非核心点内容按需生成）；不在体系内才回退默认。
 * - 「开始学习」→ mode='flow'（有序流，从当前未完成步接着学）
 * - 「查看资源」→ mode='browse'（自由浏览资源中枢，可带落点 Tab）
 * - 省略 mode（如知识图谱点节点）→ 无显式意图，资源页落「资源总览」首屏。
 */
export function setResourceNav(nextKpId: string, mode?: ResourceMode, nextEntryTab?: string): void {
  kpId = kpById(nextKpId) || ksPointById(nextKpId) ? nextKpId : CURRENT_KP_ID
  entryMode = mode ?? null
  entryTab = nextEntryTab ?? null
}

/** 资源页当前知识点 id（粘性）。 */
export function getResourceKpId(): string {
  return kpId
}

/**
 * 资源页进入意图（一次性：读后即清，与 entryTab 同口径）。
 * 返回 'flow'/'browse' 表示来源页有明确意图（直达对应视图）；null 表示无意图（落「资源总览」）。
 * 清空延后到微任务：StrictMode（dev）同步双调用初始化器时两次读到同值，真实后续导航读到 null。
 */
export function consumeResourceMode(): ResourceMode | null {
  const m = entryMode
  if (m !== null) queueMicrotask(() => { entryMode = null })
  return m
}

/** 资源页落点 Tab（一次性：读取后清空）。
 * 清空延后到微任务执行：React StrictMode（dev）会同步双调用 useState 初始化器，
 * 若读时立即清空，第二次调用拿到 null → 落点 Tab 丢失。延后清空使同一同步批次内
 * 两次读取拿到相同值，真实的后续导航（远晚于微任务）仍读到已清空的 null。 */
export function consumeResourceEntryTab(): string | null {
  const t = entryTab
  if (t !== null) queueMicrotask(() => { entryTab = null })
  return t
}
