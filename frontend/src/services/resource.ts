/**
 * 学习资源 + 测验数据获取层
 * （接口文档第 8 章讲义/外部资源 / 第 9 章测验/错题强化，接口 16/17/21/23/24/25）。
 */
import { apiGet, apiPost } from './api'
import type { QuizQuestion, ShortAnswerGrade } from '../components/QuizRenderer'
import type { ExternalResource } from '../components/ResourceAggregator'

export interface LectureData {
  kpId: string
  difficulty: string
  markdown: string
  sources: { title: string; type: string; confidence: number }[]
  hallucinationRate: number
  /** B10：产出该份讲义的工作流 trace id（直出 mock 为 null，工作流回写后为字符串）。 */
  workflowId: string | null
}

/** 8.2 生成自适应讲义（difficulty: 入门|初级|高级）。 */
export function getLecture(kpId: string, difficulty: string): Promise<LectureData> {
  return apiPost<LectureData>('/resource/lecture', { kpId, difficulty })
}

/** 9.1 获取测验题（含 correct_answer / explanation，契约 2.5）。 */
export function getQuiz(kpId: string): Promise<{ questions: QuizQuestion[] }> {
  return apiGet<{ questions: QuizQuestion[] }>(`/quiz/${kpId}`)
}

export interface QuizSubmitResult {
  /** 综合得分 0-100（客观题 + 简答 AI 折算，≥60 通过） */
  score: number
  passed: boolean
  /** 客观题答对数（简答不计入此项） */
  correctCount: number
  total: number
  wrong: QuizQuestion[]
  /** 简答题 AI 评分明细（每题 0-100 + 点评） */
  shortAnswers: ShortAnswerGrade[]
  masteryUpdated: { id: string; status: string } | null
}

/** 9.1 提交作答并判分。≥70 后端联动掌握度置 passed。 */
export function submitQuiz(
  kpId: string,
  answers: Record<string, string | string[]>
): Promise<QuizSubmitResult> {
  const payload = Object.entries(answers).map(([question_id, answer]) => ({ question_id, answer }))
  return apiPost<QuizSubmitResult>(`/quiz/${kpId}/submit`, { answers: payload })
}

/** 8.5 知识图解（Mermaid）：经 LLMClient 按当前知识点真实生成（mock/真实双模式）。 */
export function getDiagram(kpId: string): Promise<{ mermaid: string }> {
  return apiGet<{ mermaid: string }>(`/resource/diagram/${kpId}`)
}

/** 8.6 外部资源聚合（聚合 Agent 检索 + 审核 Agent 评分，已按相关度降序）。 */
export function getExternalResources(kpId: string): Promise<ExternalResource[]> {
  return apiGet<ExternalResource[]>(`/resource/external/${kpId}`)
}

/** 8.6 增量：外部资源 AI 联网搜索聚合（按 kp + 薄弱点，后端代理联网，无能力→种子兜底）。 */
export interface ExternalAggregateResult {
  kpId: string
  kpName: string
  /** 搜索 provider 名（none=未配置搜索 API） */
  provider: string
  /** 是否真实联网命中（false=种子离线兜底） */
  online: boolean
  items: ExternalResource[]
}
export function aggregateExternalResources(
  kpId: string,
  opts?: { query?: string; weakPoints?: string[] }
): Promise<ExternalAggregateResult> {
  return apiPost<ExternalAggregateResult>('/resource/external/aggregate', {
    kpId,
    query: opts?.query,
    weakPoints: opts?.weakPoints ?? [],
  })
}

/** 8.6 增量：聚合结果的会话级缓存快照（items + 是否真实联网命中）。 */
export interface AggregateSnapshot {
  items: ExternalResource[]
  online: boolean
}

/* 同一知识点的联网聚合「生成一次、会话内复用」+ 在途请求去重：
   资源推荐弹层每次打开都重挂载（页面条件渲染），且 hub「立即查看」预生成与弹层
   自身拉取此前各打一次后端 —— 相同 kpId 共享同一缓存/在途 Promise 后只联网一次；
   仅显式「重新联网搜索」（force）才重拉。 */
const aggCache = new Map<string, AggregateSnapshot>()
const aggInflight = new Map<string, Promise<AggregateSnapshot>>()

/** 读缓存（无副作用）：命中返回快照，未命中 undefined。 */
export function getAggregateCached(kpId: string): AggregateSnapshot | undefined {
  return aggCache.get(kpId)
}

/** 缓存优先的联网聚合：命中直接复用；未命中共享在途请求（并发/双挂载只发一次）。 */
export function loadAggregateCached(kpId: string, force = false): Promise<AggregateSnapshot> {
  const hit = aggCache.get(kpId)
  if (hit && !force) return Promise.resolve(hit)
  let p = aggInflight.get(kpId)
  if (!p) {
    p = aggregateExternalResources(kpId)
      .then((res) => {
        const snap: AggregateSnapshot = { items: res.items, online: res.online }
        aggCache.set(kpId, snap)
        return snap
      })
      .finally(() => aggInflight.delete(kpId))
    aggInflight.set(kpId, p)
  }
  return p
}

/** 9.2 错题强化卡：薄弱点 + 强化讲解 + 一道针对性追加练习。 */
export interface ReinforceCard {
  questionId: string
  point: string
  recap: string
  practice: QuizQuestion
}

/** 9.2 错题强化生成（诊断 Agent 定位薄弱 + 生成 Agent 产出强化内容）。 */
export function reinforce(kpId: string, wrongQuestionIds: string[]): Promise<ReinforceCard[]> {
  return apiPost<ReinforceCard[]>('/reinforce', { kpId, wrongQuestionIds })
}

/** 8.3 旁白段（接口字段为 frame，此处已映射为前端 Remotion 组件使用的 from）。 */
export interface VideoNarrationLine {
  from: number
  text: string
}

/** 8.3 分镜场景：标题 + 要点文本 + 旁白（由后端按知识点动态生成，供通用模板渲染）。 */
export interface VideoScene {
  frame: number
  title: string
  points: string[]
  narration: string
}

/** 8.3 视频讲解响应（videoUrl 为 null 时走前端 Remotion Player + TTS）。 */
export interface VideoData {
  videoUrl: string | null
  /** 视频标题（= 知识点名） */
  title: string
  /** 分镜脚本：3-5 个场景，画面标题/要点/旁白随知识点变化 */
  scenes: VideoScene[]
  narration: VideoNarrationLine[]
  fps: number
  width: number
  height: number
  durationInFrames: number
}

/** 8.3 生成视频讲解：取分镜脚本 + 帧-旁白同步映射（narration[].frame → from 在本层完成映射）。 */
export async function getVideo(kpId: string, difficulty: string): Promise<VideoData> {
  const raw = await apiPost<Omit<VideoData, 'narration'> & { narration: { frame: number; text: string }[] }>(
    '/resource/video',
    { kpId, difficulty }
  )
  return { ...raw, narration: raw.narration.map((n) => ({ from: n.frame, text: n.text })) }
}

/** 8.3 增量：服务端渲染 mp4 触发响应（CC-video-mp4 第二步）。 */
export interface VideoRenderResult {
  /** ready=已有 mp4 可直接播放/下载；rendering=后台渲染中（轮询 taskId）；unavailable=无渲染能力，回落实时 Player */
  status: 'ready' | 'rendering' | 'unavailable'
  taskId: string | null
  /** ready 时为 mp4 可访问 URL（/api/v1/media/video/xxx.mp4） */
  videoUrl: string | null
}

/** 8.3 增量：触发讲解视频服务端渲染为一体 mp4。命中缓存秒回 ready；无能力回 unavailable（前端降级）。 */
export function startVideoRender(kpId: string, difficulty: string): Promise<VideoRenderResult> {
  return apiPost<VideoRenderResult>('/resource/video/render', { kpId, difficulty })
}

/** 15.2 异步任务状态（轮询）。视频渲染任务 succeeded 时 result = { videoUrl }。 */
export interface TaskStatus<T = unknown> {
  taskId: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  progress?: number
  result: T | null
  error: { code: number; message: string } | null
}

/** 15.2 查询异步任务状态。 */
export function getTaskStatus<T = unknown>(taskId: string): Promise<TaskStatus<T>> {
  return apiGet<TaskStatus<T>>(`/tasks/${taskId}`)
}
