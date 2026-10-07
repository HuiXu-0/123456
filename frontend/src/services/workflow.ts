/**
 * 多智能体工作流数据获取层（接口文档第 11 章，接口 27/28）。
 *
 * - 11.1 `POST /workflow/execute`：触发后台 LangGraph 工作流，立即返回 workflowId；
 * - 11.2 `WS /api/v1/ws/workflow/{id}`：连接即先收一帧全量快照，之后节点进入/退出/
 *   重试/降级时推同结构增量帧（messages 仅含新增、agents/stats/phase 为当前全量），
 *   每帧套统一信封 {code, message, data, traceId}，complete 帧后服务端正常关闭。
 */
import { apiGet, apiPost } from './api'

export type WorkflowAgentStatus = 'idle' | 'running' | 'success' | 'error'

export interface WorkflowAgentState {
  id: string
  name: string
  status: WorkflowAgentStatus
  lastAction: string
}

export interface WorkflowFrameMessage {
  id: number
  from: string
  to: string
  message: string
  type: 'request' | 'response' | 'error'
  timestamp: string
}

export interface WorkflowStats {
  completedAgents: number
  messageCount: number
  progress: number
}

/** 11.2 快照（轮询响应与 WS 帧 data 同结构，严格 6 键）。 */
export interface WorkflowSnapshot {
  workflowId: string
  phase: 'idle' | 'diagnosis' | 'generation' | 'validation' | 'complete'
  step: number
  agents: WorkflowAgentState[]
  messages: WorkflowFrameMessage[]
  stats: WorkflowStats
}

/** 11.1 触发工作流执行（targetJobId / kpId / difficulty 均可选，后端缺省 kpId=nn / 初级）。
 *  B10：difficulty 决定产物回写讲义缓存的难度档（complete 且未降级时覆盖）。 */
export function executeWorkflow(
  body: { targetJobId?: string; kpId?: string; difficulty?: string } = {}
): Promise<{ workflowId: string }> {
  return apiPost<{ workflowId: string }>('/workflow/execute', body)
}

/** 11.2 轮询取一帧工作流快照（B10：供大屏回放已完成工作流，无需建 WS）。 */
export function getWorkflowSnapshot(workflowId: string): Promise<WorkflowSnapshot> {
  return apiGet<WorkflowSnapshot>(`/workflow/${workflowId}`)
}

interface WorkflowSocketHandlers {
  /** 每帧快照（首帧全量、后续 messages 仅增量，调用方按 id 去重追加）。 */
  onFrame: (snap: WorkflowSnapshot) => void
  /** 收到 complete 帧（此后服务端正常关闭，不再视为异常断开）。 */
  onComplete: () => void
  /** 连接失败 / 中途断开 / 信封业务码非 0（调用方回退演示模式）。 */
  onFail: (reason: string) => void
}

interface WorkflowSocketHandle {
  /** 主动关闭（组件卸载 / 手动重置），不会触发 onFail。 */
  close: () => void
}

/** 建立 11.2 WS 订阅。经 Vite `/api` 代理同源连接，与 REST 同走 :8000。 */
export function connectWorkflowSocket(
  workflowId: string,
  handlers: WorkflowSocketHandlers
): WorkflowSocketHandle {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${window.location.host}/api/v1/ws/workflow/${workflowId}`)
  /** 已完成 / 已失败 / 已主动关闭后，不再向上回调（complete 后的正常关闭不算断开） */
  let settled = false

  const fail = (reason: string) => {
    if (settled) return
    settled = true
    handlers.onFail(reason)
    try {
      ws.close()
    } catch {
      /* 忽略重复关闭 */
    }
  }

  ws.onmessage = (ev) => {
    if (settled) return
    let env: { code: number; message: string; data: WorkflowSnapshot }
    try {
      env = JSON.parse(ev.data as string)
    } catch {
      fail('推送帧解析失败')
      return
    }
    if (env.code !== 0) {
      fail(env.message || `工作流推送异常（code ${env.code}）`)
      return
    }
    handlers.onFrame(env.data)
    if (env.data.phase === 'complete') {
      settled = true
      handlers.onComplete()
    }
  }
  ws.onerror = () => fail('实时通道连接失败')
  ws.onclose = () => fail('实时通道中途断开')

  return {
    close: () => {
      settled = true
      try {
        ws.close()
      } catch {
        /* 忽略 */
      }
    },
  }
}
