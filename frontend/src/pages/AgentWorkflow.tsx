import { useState, useRef, useLayoutEffect, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { gsap } from 'gsap'
import { MotionPathPlugin } from 'gsap/MotionPathPlugin'
import { useGSAP } from '@gsap/react'
import AgentStatusCard from '../components/AgentStatusCard'
import ResourceTypeIcon from '../components/ResourceTypeIcon'
import CountUp from '../components/CountUp'
import PageHeader from '../components/PageHeader'
import { RevealGroup, RevealItem } from '../components/Reveal'
import { USE_REAL_API } from '../services/api'
import {
  executeWorkflow,
  connectWorkflowSocket,
  getWorkflowSnapshot,
  type WorkflowSnapshot,
} from '../services/workflow'
import { consumeWorkflowReplay } from '../services/workflowNav'
import { setResourceNav } from '../services/resourceNav'
import { KNOWLEDGE_POINTS, CURRENT_KP_ID, kpById } from '../data/knowledgePoints'
import { useMastery } from '../store/mastery'
import LearningEvalPanel from '../components/LearningEvalPanel'
import {
  getLearningEvaluation,
  synthesizeEvaluation,
  type LearningEvaluation,
} from '../services/learningEval'
import type { PageType } from '../App'
import './AgentWorkflow.css'

/* B10：大屏实时模式可选目标难度档（与资源页讲义三档一致） */
const DIFFICULTIES = ['入门', '初级', '高级'] as const

/* 工作流完成产物 → 资源页对应 Tab（id 与 LearningResource 的 TAB_GROUPS 对齐） */
const WORKFLOW_ARTIFACTS = [
  { tab: 'lecture', label: '定制讲义', desc: '分阶知识讲义，按目标难度生成' },
  { tab: 'mindmap', label: '思维导图', desc: '由讲义结构化的知识脑图' },
  { tab: 'video', label: '讲解视频', desc: '配套讲解视频脚本与播放' },
  { tab: 'quiz', label: '分阶测试题', desc: '巩固检验的分阶测验题' },
  { tab: 'diagram', label: '知识图解', desc: '按主题真实生成的 Mermaid 知识脉络图' },
] as const

gsap.registerPlugin(useGSAP, MotionPathPlugin)

/* 连线定义：源节点 → 目标节点（用于测量真实坐标并生成曲线路径）*/
const EDGES = [
  { id: 'diagnosis', from: '.agent-node--orchestrator', to: '.agent-node--diagnosis' },
  { id: 'generation', from: '.agent-node--orchestrator', to: '.agent-node--generation' },
  { id: 'critic', from: '.agent-node--orchestrator', to: '.agent-node--critic' },
  { id: 'rag', from: '.agent-node--generation', to: '.agent-node--rag' },
]

type AgentStatus = 'idle' | 'running' | 'success' | 'error'
type WorkflowPhase = 'idle' | 'diagnosis' | 'generation' | 'validation' | 'complete'

interface AgentState {
  name: string
  status: AgentStatus
  lastAction: string
}

interface MessageLog {
  id: number
  from: string
  to: string
  message: string
  type: 'request' | 'response' | 'error'
  timestamp: Date
}

const agentConfig = [
  { id: 'diagnosis', name: '学情诊断Agent', icon: '🔍' },
  { id: 'generation', name: '领域知识生成Agent', icon: '📝' },
  { id: 'critic', name: '内容审核Agent', icon: '✓' }
]

export default function AgentWorkflow({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  const [phase, setPhase] = useState<WorkflowPhase>('idle')
  const [agents, setAgents] = useState<AgentState[]>([
    { name: '学情诊断Agent', status: 'idle', lastAction: '等待用户输入' },
    { name: '领域知识生成Agent', status: 'idle', lastAction: '等待诊断结果' },
    { name: '内容审核Agent', status: 'idle', lastAction: '等待生成内容' }
  ])
  const [messages, setMessages] = useState<MessageLog[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)

  /* B7-b 实时通道：演示=既有 setTimeout 模拟链（评审断网兜底，原样保留）；
     实时=11.1 execute + 11.2 WS 帧驱动既有 agents/messages/stats/phase 状态。
     mock 模式（USE_REAL_API=false）恒为演示且不渲染开关，行为与切换前逐帧一致 */
  const [mode, setMode] = useState<'demo' | 'live'>(USE_REAL_API ? 'live' : 'demo')
  const [liveStats, setLiveStats] = useState({ completedAgents: 0, messageCount: 0, progress: 0 })
  const [liveHint, setLiveHint] = useState('')
  const socketRef = useRef<{ close: () => void } | null>(null)
  const hintTimerRef = useRef(0)

  /* B10：实时模式目标知识点 + 难度（默认沿用现有演示参数 nn / 初级）；
     完成后给出「已更新至学习资源」入口；回放则展示某已完成工作流的完成态。 */
  const [targetKp, setTargetKp] = useState(CURRENT_KP_ID)
  const [targetDiff, setTargetDiff] = useState<(typeof DIFFICULTIES)[number]>('初级')
  const [completed, setCompleted] = useState<{ kpId: string; degraded: boolean } | null>(null)
  /* 工作流完成弹窗：done 事件触发，列出本次产物 + 跳转资源页对应 Tab。
     degraded=true 表示三次审核仍未通过、走降级终态：仍弹窗给终态反馈，并标注降级提示。 */
  const [resultModal, setResultModal] = useState<{ kpId: string; degraded: boolean } | null>(null)
  const [replaying, setReplaying] = useState(false)
  const lastFrameRef = useRef<WorkflowSnapshot | null>(null)

  /* 学习过程评估 Agent（接口文档 12.2）：跨会话累积评估，独立于本次 LangGraph 运行路径，
     作为大屏中的一个独立 Agent 区块呈现其最新多维评估产出。联调取后端真实行为评估；
     mock / 后端不可用 → 由掌握度本地合成（同结构），保证大屏始终能体现评估能力。 */
  const masteryStatus = useMastery((s) => s.status)
  const [evaluation, setEvaluation] = useState<LearningEvaluation | null>(null)
  useEffect(() => {
    if (!USE_REAL_API) return
    let alive = true
    getLearningEvaluation()
      .then((d) => alive && setEvaluation(d))
      .catch((e) => console.error('[workflow] 加载学习评估失败，回退本地合成', e))
    return () => {
      alive = false
    }
  }, [masteryStatus])
  const masteredIds = useMemo(
    () => KNOWLEDGE_POINTS.filter((k) => masteryStatus[k.id] === 'passed').map((k) => k.id),
    [masteryStatus]
  )
  const evalData =
    USE_REAL_API && evaluation
      ? evaluation
      : synthesizeEvaluation(
          KNOWLEDGE_POINTS.map((k) => ({ id: k.id, name: k.name })),
          masteredIds
        )

  /* GSAP G1：测量节点真实坐标 → 生成曲线连线路径 → 粒子沿路径流动 */
  const networkRef = useRef<HTMLDivElement>(null)
  const [edgePaths, setEdgePaths] = useState<{ id: string; d: string }[]>([])

  useLayoutEffect(() => {
    const net = networkRef.current
    if (!net) return
    const measure = () => {
      const base = net.getBoundingClientRect()
      const center = (sel: string) => {
        const el = net.querySelector(sel) as HTMLElement | null
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { x: r.left - base.left + r.width / 2, y: r.top - base.top + r.height / 2 }
      }
      const curve = (a: { x: number; y: number }, b: { x: number; y: number }) => {
        const dx = b.x - a.x, dy = b.y - a.y
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
        const k = 0.14 // 垂直方向轻微弧度
        const cx = mx - dy * k, cy = my + dx * k
        return `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`
      }
      const paths = EDGES
        .map((e) => ({ e, a: center(e.from), b: center(e.to) }))
        .filter((x) => x.a && x.b)
        .map((x) => ({ id: x.e.id, d: curve(x.a!, x.b!) }))
      setEdgePaths(paths)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(net)
    return () => ro.disconnect()
  }, [])

  /* B7-b 实时模式下粒子仅在 running 边流动：三条 Agent 边随对应节点 running 点亮，
     rag 边随生成节点；演示模式为 null（保持全边流动，与切换前逐帧一致） */
  const liveActiveEdges =
    mode === 'live'
      ? new Set([
          ...agentConfig.filter((_, i) => agents[i].status === 'running').map((c) => c.id),
          ...(agents[1].status === 'running' ? ['rag'] : []),
        ])
      : null
  /* 实时模式 running 边集合变化时重建粒子时间线；演示模式恒为 '' 不触发额外重建 */
  const liveEdgesKey = liveActiveEdges ? Array.from(liveActiveEdges).sort().join() : ''

  /* 运行态：粒子沿每条连线路径流动（淡入 → 沿路径 → 淡出，循环）*/
  useGSAP(() => {
    const net = networkRef.current
    if (!net || !isRunning || edgePaths.length === 0) return
    const tls: gsap.core.Timeline[] = []
    edgePaths.forEach((p) => {
      const dots = net.querySelectorAll<SVGCircleElement>(`.msg-dot[data-edge="${p.id}"]`)
      dots.forEach((dot, di) => {
        const tl = gsap.timeline({ repeat: -1, delay: di * 0.85 })
        tl.set(dot, { opacity: 0 })
          .to(dot, { opacity: 1, duration: 0.25 }, 0)
          .to(dot, {
            duration: 1.7,
            ease: 'none',
            motionPath: { path: `#edge-path-${p.id}`, align: `#edge-path-${p.id}`, alignOrigin: [0.5, 0.5] },
          }, 0)
          .to(dot, { opacity: 0, duration: 0.25 }, 1.45)
        tls.push(tl)
      })
    })
    return () => tls.forEach((t) => t.kill())
  }, { dependencies: [isRunning, edgePaths, liveEdgesKey], scope: networkRef })

  const simulateWorkflow = () => {
    setIsRunning(true)
    setPhase('diagnosis')
    setMessages([])
    setResultModal(null)

    // Phase 1: Diagnosis
    setAgents(prev => [
      { name: '学情诊断Agent', status: 'running', lastAction: '分析用户画像数据...' },
      prev[1],
      prev[2]
    ])
    setCurrentStep(1)

    setTimeout(() => {
      addMessage('用户', '学情诊断Agent', '提交学习者画像数据', 'request')
    }, 500)

    setTimeout(() => {
      addMessage('学情诊断Agent', '领域知识生成Agent', '诊断完成：识别知识盲区3处', 'response')
      setAgents(prev => [
        { name: '学情诊断Agent', status: 'success', lastAction: '诊断完成：识别3处盲区' },
        prev[1],
        prev[2]
      ])
    }, 2000)

    // Phase 2: Generation
    setTimeout(() => {
      setPhase('generation')
      setCurrentStep(2)
      setAgents(prev => [
        prev[0],
        { name: '领域知识生成Agent', status: 'running', lastAction: '生成个性化教学资源...' },
        prev[2]
      ])
      addMessage('领域知识生成Agent', 'RAG系统', '检索相关知识片段', 'request')
    }, 2500)

    setTimeout(() => {
      addMessage('RAG系统', '领域知识生成Agent', '返回12篇匹配文档', 'response')
    }, 3000)

    setTimeout(() => {
      addMessage('领域知识生成Agent', '内容审核Agent', '提交生成内容进行审核', 'response')
      setAgents(prev => [
        prev[0],
        { name: '领域知识生成Agent', status: 'success', lastAction: '生成讲义与测试题完成' },
        prev[2]
      ])
    }, 4000)

    // Phase 3: Validation
    setTimeout(() => {
      setPhase('validation')
      setCurrentStep(3)
      setAgents(prev => [
        prev[0],
        prev[1],
        { name: '内容审核Agent', status: 'running', lastAction: '验证内容准确性与合规性...' }
      ])
    }, 4500)

    setTimeout(() => {
      addMessage('内容审核Agent', '领域知识生成Agent', '交叉验证：内容通过审核', 'response')
      setAgents(prev => [
        prev[0],
        prev[1],
        { name: '内容审核Agent', status: 'success', lastAction: '审核通过：无幻觉风险' }
      ])
    }, 5500)

    // Complete
    setTimeout(() => {
      setPhase('complete')
      setCurrentStep(4)
      setIsRunning(false)
      setResultModal({ kpId: targetKp, degraded: false })
    }, 6000)
  }

  const addMessage = (from: string, to: string, message: string, type: 'request' | 'response' | 'error') => {
    setMessages(prev => [
      ...prev,
      {
        id: prev.length + 1,
        from,
        to,
        message,
        type,
        timestamp: new Date()
      }
    ])
  }

  /* ---------- B7-b 实时模式（演示模式上方逻辑零改动） ---------- */

  /** 轻提示（页头徽章位展示，4 秒自动消失）。 */
  const showHint = (text: string) => {
    setLiveHint(text)
    window.clearTimeout(hintTimerRef.current)
    hintTimerRef.current = window.setTimeout(() => setLiveHint(''), 4000)
  }

  const closeSocket = () => {
    socketRef.current?.close()
    socketRef.current = null
  }

  /** WS 帧（首帧全量、后续 messages 仅增量）→ 驱动既有状态卡 / 日志 / stats / phase。 */
  const applyFrame = (snap: WorkflowSnapshot) => {
    lastFrameRef.current = snap
    setPhase(snap.phase)
    setCurrentStep(snap.step)
    setLiveStats(snap.stats)
    setAgents((prev) =>
      agentConfig.map((cfg, i) => {
        const a = snap.agents.find((x) => x.id === cfg.id)
        return a ? { name: a.name, status: a.status, lastAction: a.lastAction } : prev[i]
      })
    )
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.id))
      const fresh = snap.messages
        .filter((m) => !seen.has(m.id))
        .map((m) => ({
          id: m.id,
          from: m.from,
          to: m.to,
          message: m.message,
          type: m.type,
          timestamp: new Date(m.timestamp),
        }))
      return fresh.length ? [...prev, ...fresh] : prev
    })
  }

  /** 实时失败（execute 报错 / WS 连不上或中途断开）→ 自动回退演示模式并轻提示。 */
  const fallbackToDemo = (reason: string) => {
    closeSocket()
    setMode('demo')
    showHint(`${reason}，已自动回退演示模式`)
    // 清掉可能残留的实时半程状态，再走原模拟链
    setAgents([
      { name: '学情诊断Agent', status: 'idle', lastAction: '等待用户输入' },
      { name: '领域知识生成Agent', status: 'idle', lastAction: '等待诊断结果' },
      { name: '内容审核Agent', status: 'idle', lastAction: '等待生成内容' }
    ])
    simulateWorkflow()
  }

  /** 实时模式启动：11.1 execute（目标 kp+难度）→ 11.2 WS 订阅，节点随帧实时点亮。 */
  const startLiveWorkflow = async () => {
    setIsRunning(true)
    setPhase('diagnosis')
    setCurrentStep(0)
    setMessages([])
    setLiveStats({ completedAgents: 0, messageCount: 0, progress: 0 })
    setCompleted(null)
    setResultModal(null)
    setReplaying(false)
    lastFrameRef.current = null
    let workflowId: string
    try {
      workflowId = (await executeWorkflow({ kpId: targetKp, difficulty: targetDiff })).workflowId
    } catch (e) {
      console.error('[workflow] 触发实时工作流失败', e)
      fallbackToDemo('后端不可用')
      return
    }
    socketRef.current = connectWorkflowSocket(workflowId, {
      onFrame: applyFrame,
      onComplete: () => {
        socketRef.current = null
        setIsRunning(false)
        // B10：未降级 → 产物已回写学习资源（critic 红灯=降级则旧资源保留）
        const critic = lastFrameRef.current?.agents.find((a) => a.id === 'critic')
        const degraded = critic?.status === 'error'
        setCompleted({ kpId: targetKp, degraded })
        // issue#4：无论是否降级都给终态弹窗——降级（三次审核仍未通过）也要弹窗展示
        // 降级生成的产物并明确标注，避免用户卡在没有任何终态反馈。
        if (degraded) showHint('内容审核未完全通过，已降级生成（学习资源保留上一版本）')
        setResultModal({ kpId: targetKp, degraded })
      },
      onFail: (reason) => {
        console.error('[workflow] 实时通道异常：', reason)
        socketRef.current = null
        fallbackToDemo(reason)
      },
    })
  }

  /** B10：完成后跳转学习资源页查看回写产物（设目标 kp 后导航）。 */
  const goToResource = () => {
    if (!completed) return
    setResourceNav(completed.kpId, 'browse')
    onNavigate?.('learning-resource')
  }

  /** 完成弹窗：跳转资源页对应 Tab（缺省落讲义）。 */
  const goToResourceTab = (tab?: string) => {
    const kpId = resultModal?.kpId ?? targetKp
    setResourceNav(kpId, 'browse', tab)
    setResultModal(null)
    onNavigate?.('learning-resource')
  }

  const startWorkflow = () => {
    if (mode === 'live') void startLiveWorkflow()
    else simulateWorkflow()
  }

  /* 卸载清理：断开 WS、清提示定时器（close 不触发回退） */
  useEffect(() => {
    return () => {
      socketRef.current?.close()
      window.clearTimeout(hintTimerRef.current)
    }
  }, [])

  /* B10：从资源页「查看生成过程」进入 → 回放该已完成工作流（取完成态快照渲染）。 */
  useEffect(() => {
    const replayId = consumeWorkflowReplay()
    if (!replayId) return
    setReplaying(true)
    getWorkflowSnapshot(replayId)
      .then((snap) => {
        applyFrame(snap)
        setIsRunning(false)
      })
      .catch((e) => {
        console.error('[workflow] 回放工作流失败', e)
        setReplaying(false)
        // error 态兜底：回放失败时给出提示，而非静默停在初始态（issue#2）
        showHint('回放生成过程失败，可在上方重新启动工作流')
      })
    // 仅挂载时执行一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ---------- B7-b 实时模式逻辑结束 ---------- */

  const resetWorkflow = () => {
    closeSocket()
    setPhase('idle')
    setCurrentStep(0)
    setIsRunning(false)
    setCompleted(null)
    setResultModal(null)
    setReplaying(false)
    lastFrameRef.current = null
    setAgents([
      { name: '学情诊断Agent', status: 'idle', lastAction: '等待用户输入' },
      { name: '领域知识生成Agent', status: 'idle', lastAction: '等待诊断结果' },
      { name: '内容审核Agent', status: 'idle', lastAction: '等待生成内容' }
    ])
    setMessages([])
    setLiveStats({ completedAgents: 0, messageCount: 0, progress: 0 })
  }

  const getPhaseLabel = () => {
    switch (phase) {
      case 'idle': return '等待启动'
      case 'diagnosis': return '学情诊断阶段'
      case 'generation': return '资源生成阶段'
      case 'validation': return '内容审核阶段'
      case 'complete': return '工作流完成'
    }
  }

  return (
    <div className="workflow-page">
      {/* 统一标题区：锚条 + 高亮 + 运行状态徽章 + 启动/重置 */}
      <PageHeader
        title="智能体协同调度大屏"
        highlight="协同调度"
        subtitle="实时查看多智能体工作流执行状态与决策逻辑"
        crumb="Agent工作流"
        onBack={() => onNavigate?.('dashboard')}
        badges={[
          ...(liveHint ? [{ label: '联调提示', value: liveHint, tone: 'accent' as const }] : []),
          {
            label: '运行状态',
            value: isRunning ? '运行中' : phase === 'complete' ? '已完成' : '等待启动',
            tone: isRunning ? 'accent' : phase === 'complete' ? 'safe' : 'default',
          },
        ]}
        actions={
          <>
            {USE_REAL_API && (
              <button
                className="workflow-btn workflow-btn--secondary"
                onClick={() => setMode((m) => (m === 'live' ? 'demo' : 'live'))}
                disabled={isRunning}
                title="实时=后端 LangGraph 工作流 WS 推送；演示=本地模拟链（断网兜底）"
              >
                {mode === 'live' ? '实时模式' : '演示模式'}
              </button>
            )}
            <button
              className={`workflow-btn ${isRunning ? 'workflow-btn--disabled' : 'workflow-btn--primary'}`}
              onClick={startWorkflow}
              disabled={isRunning}
            >
              {isRunning ? '执行中...' : mode === 'live' ? '启动工作流' : '启动演示'}
            </button>
            <button className="workflow-btn workflow-btn--secondary" onClick={resetWorkflow}>
              重置状态
            </button>
          </>
        }
      />

      {/* B10：实时模式目标选择 + 回放/完成提示（仅联调实时模式渲染，演示模式零变化） */}
      {USE_REAL_API && mode === 'live' && (
        <div className="workflow-live-bar">
          {replaying ? (
            <span className="workflow-live-bar__replay">
              <span className="workflow-live-bar__dot" />
              正在回放已完成工作流 · 展示完成态轨迹
            </span>
          ) : (
            <div className="workflow-live-bar__target">
              <span className="workflow-live-bar__label">目标知识点</span>
              <select
                className="workflow-live-bar__select"
                value={targetKp}
                onChange={(e) => setTargetKp(e.target.value)}
                disabled={isRunning}
              >
                {KNOWLEDGE_POINTS.map((kp) => (
                  <option key={kp.id} value={kp.id}>
                    {kp.name}
                  </option>
                ))}
              </select>
              <span className="workflow-live-bar__label">难度</span>
              <select
                className="workflow-live-bar__select"
                value={targetDiff}
                onChange={(e) => setTargetDiff(e.target.value as (typeof DIFFICULTIES)[number])}
                disabled={isRunning}
              >
                {DIFFICULTIES.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </div>
          )}
          <AnimatePresence>
            {completed && !completed.degraded && (
              <motion.div
                className="workflow-live-bar__toast"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
              >
                <span className="workflow-live-bar__toast-text">
                  ✓ 生成结果已更新至学习资源（{kpById(completed.kpId)?.name ?? completed.kpId}）
                </span>
                <button className="workflow-live-bar__toast-btn" onClick={goToResource}>
                  去学习资源查看 →
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      <RevealGroup>
      {/* Phase Indicator */}
      <RevealItem className="workflow-phase">
        <div className="workflow-phase__steps">
          {['诊断', '生成', '审核', '完成'].map((step, index) => (
            <div
              key={step}
              className={`workflow-step ${currentStep > index ? 'workflow-step--completed' : currentStep === index + 1 ? 'workflow-step--active' : ''}`}
            >
              <div className="workflow-step__marker">
                {currentStep > index + 1 ? (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <span>{index + 1}</span>
                )}
              </div>
              <span className="workflow-step__label">{step}</span>
            </div>
          ))}
        </div>
        <div className="workflow-phase__current">
          <motion.span
            key={phase}
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
          >
            {getPhaseLabel()}
          </motion.span>
        </div>
      </RevealItem>

      <RevealItem className="workflow-content">
        {/* Agent Network Visualization */}
        <div className="workflow-visualization">
          <div className="agent-network" ref={networkRef}>
            {/* Central Orchestration */}
            <div className="agent-node agent-node--orchestrator">
              <motion.div
                className="agent-node__icon"
                animate={isRunning ? { scale: [1, 1.05, 1] } : {}}
                transition={{ duration: 2, repeat: Infinity }}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 1v6M12 17v6M4.22 4.22l4.24 4.24M15.54 15.54l4.24 4.24M1 12h6M17 12h6M4.22 19.78l4.24-4.24M15.54 8.46l4.24-4.24" />
                </svg>
              </motion.div>
              <span className="agent-node__label">LangGraph编排器</span>
            </div>

            {/* Three Agents */}
            {agentConfig.map((agent, index) => {
              const state = agents[index]
              const isActive = state.status === 'running'

              return (
                <motion.div
                  key={agent.id}
                  className={`agent-node agent-node--${agent.id} agent-node--${state.status}`}
                  animate={isActive ? {
                    boxShadow: [
                      '0 0 0 0 rgba(37, 99, 235, 0)',
                      '0 0 24px 8px rgba(37, 99, 235, 0.28)',
                      '0 0 0 0 rgba(37, 99, 235, 0)'
                    ]
                  } : {}}
                  transition={{ duration: 1.5, repeat: Infinity }}
                >
                  <div className="agent-node__header">
                    <span className="agent-node__emoji">{agent.icon}</span>
                    <span className={`status-dot status-dot--${state.status}`} />
                  </div>
                  <span className="agent-node__name">{agent.name}</span>
                  <span className="agent-node__action">{state.lastAction}</span>
                </motion.div>
              )
            })}

            {/* RAG System */}
            <div className="agent-node agent-node--rag">
              <div className="agent-node__icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                  <line x1="8" y1="7" x2="16" y2="7" />
                  <line x1="8" y1="11" x2="16" y2="11" />
                </svg>
              </div>
              <span className="agent-node__label">RAG知识库</span>
            </div>

            {/* Connection Lines */}
            <div className="network-lines">
              <svg className={`network-svg ${isRunning ? 'network-svg--active' : ''}`}>
                <defs>
                  {/* 蓝紫流光渐变 + 发光滤镜 */}
                  <linearGradient id="flow-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#2563eb" />
                    <stop offset="100%" stopColor="#7c3aed" />
                  </linearGradient>
                  <filter id="line-glow" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="2.5" result="blur" />
                    <feMerge>
                      <feMergeNode in="blur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>
                {/* 连线路径（坐标由真实节点中心测量得到）*/}
                {edgePaths.map((p) => (
                  <path
                    key={p.id}
                    id={`edge-path-${p.id}`}
                    className={`network-line network-line--to-${p.id}`}
                    d={p.d}
                    fill="none"
                  />
                ))}
                {/* GSAP MotionPath 消息粒子（运行态沿连线流动；实时模式仅 running 边渲染）*/}
                {isRunning &&
                  edgePaths
                    .filter((p) => !liveActiveEdges || liveActiveEdges.has(p.id))
                    .map((p) =>
                      [0, 1].map((di) => (
                        <circle key={`${p.id}-${di}`} className="msg-dot" data-edge={p.id} r="3.5" />
                      ))
                    )}
              </svg>
            </div>
          </div>
        </div>

        {/* Agent Status List */}
        <div className="workflow-sidebar">
          <div className="sidebar-section">
            <h3>智能体状态</h3>
            <div className="agent-status-list">
              {agents.map((agent, index) => (
                <motion.div
                  key={index}
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.1 }}
                >
                  <AgentStatusCard
                    name={agent.name}
                    status={agent.status}
                    lastAction={agent.lastAction}
                  />
                </motion.div>
              ))}
            </div>
          </div>

          <div className="sidebar-section">
            <h3>消息日志</h3>
            <div className="message-log">
              <AnimatePresence>
                {messages.map((msg) => (
                  <motion.div
                    key={msg.id}
                    className={`message-item message-item--${msg.type}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                  >
                    <div className="message-item__header">
                      <span className="message-item__from">{msg.from}</span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="message-item__arrow">
                        <path d="M5 12h14M12 5l7 7-7 7" />
                      </svg>
                      <span className="message-item__to">{msg.to}</span>
                    </div>
                    <span className="message-item__content">{msg.message}</span>
                  </motion.div>
                ))}
              </AnimatePresence>
              {messages.length === 0 && (
                <div className="message-log-empty">
                  <span>暂无消息记录</span>
                </div>
              )}
            </div>
          </div>

          <div className="sidebar-section">
            <h3>工作流统计</h3>
            <div className="workflow-stats">
              <div className="workflow-stat">
                <CountUp
                  className="workflow-stat__value"
                  value={mode === 'live' ? liveStats.completedAgents : agents.filter(a => a.status === 'success').length}
                  duration={0.6}
                />
                <span className="workflow-stat__label">已完成Agent</span>
              </div>
              <div className="workflow-stat">
                <CountUp
                  className="workflow-stat__value"
                  value={mode === 'live' ? liveStats.messageCount : messages.length}
                  duration={0.6}
                />
                <span className="workflow-stat__label">消息交互数</span>
              </div>
              <div className="workflow-stat">
                <CountUp
                  className="workflow-stat__value"
                  value={mode === 'live' ? liveStats.progress : phase === 'complete' ? 100 : phase === 'idle' ? 0 : currentStep * 25}
                  suffix="%"
                  duration={0.6}
                />
                <span className="workflow-stat__label">工作流进度</span>
              </div>
            </div>
          </div>
        </div>
      </RevealItem>

      {/* 学习过程评估 Agent：作为多智能体系统的独立参与方呈现（跨会话累积评估，
          非单次生成工作流的编排节点），展示其最新多维评估输出，体现系统含评估能力 */}
      <RevealItem className="workflow-eval">
        <div className="workflow-eval__intro">
          <span className="workflow-eval__icon">📈</span>
          <div className="workflow-eval__intro-text">
            <h3 className="workflow-eval__title">
              学习过程评估 Agent
              <span className="workflow-eval__tag">独立 Agent · 非编排节点</span>
            </h3>
            <p className="workflow-eval__desc">
              区别于上方单次生成工作流的诊断/生成/审核节点，评估 Agent
              <strong>跨会话持续运行</strong>，累积你的做题、进度与笔记行为，产出多维学情评估与动态调整建议。
            </p>
          </div>
        </div>
        <LearningEvalPanel evaluation={evalData} onNavigate={onNavigate} />
      </RevealItem>
      </RevealGroup>

      {/* 工作流完成弹窗：done 触发，列出本次产物 + 跳转资源页对应 Tab */}
      <AnimatePresence>
        {resultModal && (
          <motion.div
            className="wf-result-mask"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setResultModal(null)}
          >
            <motion.div
              className="wf-result"
              initial={{ opacity: 0, y: 24, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 24, scale: 0.96 }}
              transition={{ type: 'spring', stiffness: 260, damping: 24 }}
              onClick={(e) => e.stopPropagation()}
            >
              <button className="wf-result__close" aria-label="关闭" onClick={() => setResultModal(null)}>
                ×
              </button>
              <div className="wf-result__head">
                <span className={`wf-result__badge${resultModal.degraded ? ' wf-result__badge--warn' : ''}`}>
                  {resultModal.degraded ? '!' : '✓'}
                </span>
                <div>
                  <h3 className="wf-result__title">
                    {resultModal.degraded ? '工作流已降级完成' : '工作流执行完成'}
                  </h3>
                  <p className="wf-result__sub">
                    {resultModal.degraded
                      ? `「${kpById(resultModal.kpId)?.name ?? resultModal.kpId}」内容审核三次仍未通过，以下为降级生成产物`
                      : `已为「${kpById(resultModal.kpId)?.name ?? resultModal.kpId}」生成下列学习产物`}
                  </p>
                </div>
              </div>

              {resultModal.degraded && (
                <div className="wf-result__notice" role="alert">
                  ⚠ 审核未完全通过（已达最大重试次数），系统已降级生成。学习资源仍保留上一稳定版本，建议人工复核后再采用。
                </div>
              )}

              <div className="wf-result__list">
                {WORKFLOW_ARTIFACTS.map((a) => (
                  <button key={a.tab} className="wf-result__item" onClick={() => goToResourceTab(a.tab)}>
                    <span className="wf-result__item-emoji"><ResourceTypeIcon kind={a.tab} size={26} /></span>
                    <span className="wf-result__item-text">
                      <span className="wf-result__item-label">{a.label}</span>
                      <span className="wf-result__item-desc">{a.desc}</span>
                    </span>
                    <span className="wf-result__item-arrow">→</span>
                  </button>
                ))}
              </div>

              <div className="wf-result__actions">
                <button className="wf-result__btn wf-result__btn--ghost" onClick={() => setResultModal(null)}>
                  留在工作流
                </button>
                <button className="wf-result__btn wf-result__btn--primary" onClick={() => goToResourceTab()}>
                  查看生成内容 →
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}