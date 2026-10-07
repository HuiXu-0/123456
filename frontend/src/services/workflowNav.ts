/**
 * 工作流回放路由传参通道（B10）。
 *
 * 与 resourceNav 同理：App 是 stage 状态机（无 URL 路由参数），页面经 AnimatePresence
 * 按 key 重挂载。资源页「查看生成过程」导航去大屏前写入 workflowId，大屏挂载时读取
 * 并回放该已完成工作流（GET /workflow/{id} 取完成态快照）。
 *
 * 一次性（读后即清）：避免侧边栏再次进入大屏时残留回放目标。
 */
let replayWorkflowId: string | null = null

/** 导航去大屏前调用：指定要回放的工作流 id。 */
export function setWorkflowReplay(workflowId: string): void {
  replayWorkflowId = workflowId
}

/** 大屏挂载时读取回放目标（读取后即清空）。 */
export function consumeWorkflowReplay(): string | null {
  const id = replayWorkflowId
  replayWorkflowId = null
  return id
}
