import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig, interpolate, spring } from 'remotion'

export const VIDEO_FPS = 30
export const VIDEO_W = 1280
export const VIDEO_H = 720
/* 单个分镜场景时长（帧，30fps→6s/场景），与后端 services.resource._SCENE_FRAMES 对齐 */
export const SCENE_FRAMES = 180

/* 分镜场景：标题 + 要点文本 + 旁白（接口文档 8.3 scenes[]，由后端按知识点动态生成） */
export interface LectureScene {
  title: string
  points: string[]
  narration: string
}

export interface LectureVideoProps {
  /* 视频标题（= 知识点名）；缺省走默认神经网络脚本 */
  title?: string
  /* 分镜脚本；无数据时回落 DEFAULT_SCENES（兜底占位） */
  scenes?: LectureScene[]
}

/* 默认脚本（无后端数据 / Remotion Studio 预览兜底）：与后端 _VIDEO_SCRIPT_NN 逐字一致 */
export const DEFAULT_TITLE = '神经网络基础'
export const DEFAULT_SCENES: LectureScene[] = [
  {
    title: '课程导入 · 神经网络基础',
    points: ['三步运算：求和 → 偏置 → 激活', '前向传播与反向传播', '由领域知识生成智能体定制'],
    narration: '欢迎学习神经网络基础。本视频由领域知识生成智能体为你定制。',
  },
  {
    title: '神经元的三步运算',
    points: ['① 加权求和 Σ wᵢ·xᵢ', '② 加上偏置 + b', '③ 激活函数 ReLU(z)'],
    narration: '一个神经元完成三步运算：加权求和、加上偏置、再经过激活函数输出。',
  },
  {
    title: '前向传播',
    points: ['输入与权重相乘求和', '加偏置得到 z', 'ReLU 激活得到输出 a'],
    narration: '前向传播时，输入与权重相乘求和，加偏置得到 z，再用 ReLU 激活得到输出。',
  },
  {
    title: '常见激活函数',
    points: ['ReLU：max(0,x)，最常用', 'Sigmoid：压缩到 (0,1)', 'Tanh：范围 (-1,1)，零均值'],
    narration: '常见激活函数有 ReLU、Sigmoid 和 Tanh，其中 ReLU 计算快、最常用。',
  },
  {
    title: '学习闭环',
    points: ['神经元 → 前向传播', '激活 → 反向传播更新', '完成测验巩固理解'],
    narration: '神经元、前向传播、激活、反向传播更新，构成了神经网络学习的完整闭环。',
  },
]

export const C = {
  bg1: '#0a1024',
  bg2: '#111a3a',
  blue: '#60a5fa',
  purple: '#a78bfa',
  green: '#34d399',
  amber: '#fbbf24',
  text: '#f1f5f9',
  sub: '#94a3b8',
}
/* 要点卡片按序轮转配色 */
const POINT_COLORS = [C.blue, C.purple, C.green, C.amber]

const fade = (frame: number, inEnd: number, outStart: number, outEnd: number) =>
  interpolate(frame, [0, inEnd, outStart, outEnd], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  })

/* 通用分镜场景：首场景作封面（大标题 + 副标题），其余为「场景标题 + 要点卡片」。
   容纳任意主题、任意要点条数；要点逐条错峰入场，底部进度点指示当前场景。 */
/* dur：本场景实际帧时长（用于淡出对齐）。缺省 SCENE_FRAMES（Player 固定 6s/场景，行为不变）；
   服务端渲染按各段 TTS 音频帧数传入变长 dur，淡出随之对齐，长旁白不会提前淡出留白。 */
export const SceneCard: React.FC<{ scene: LectureScene; index: number; total: number; videoTitle: string; dur?: number }> = ({
  scene,
  index,
  total,
  videoTitle,
  dur = SCENE_FRAMES,
}) => {
  const frame = useCurrentFrame()
  const { fps } = useVideoConfig()
  const titleSpring = spring({ frame, fps, config: { damping: 14 } })
  const isCover = index === 0
  return (
    <AbsoluteFill
      style={{
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'column',
        padding: '0 80px',
        opacity: fade(frame, 14, dur - 22, dur - 2),
      }}
    >
      <div style={{ fontSize: isCover ? 26 : 20, color: C.blue, letterSpacing: 4, marginBottom: 14, opacity: titleSpring }}>
        {isCover ? 'AI 讲解视频' : `场景 ${index + 1} / ${total}`}
      </div>
      <div
        style={{
          fontSize: isCover ? 70 : 46,
          fontWeight: 800,
          color: C.text,
          textAlign: 'center',
          transform: `scale(${0.85 + titleSpring * 0.15})`,
          marginBottom: isCover ? 14 : 30,
        }}
      >
        {isCover ? videoTitle : scene.title}
      </div>
      {isCover && <div style={{ fontSize: 24, color: C.sub, marginBottom: 28 }}>{scene.title}</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, width: '100%', maxWidth: 900 }}>
        {scene.points.map((p, i) => {
          const s = spring({ frame: frame - 8 - i * 12, fps, config: { damping: 13 } })
          const color = POINT_COLORS[i % POINT_COLORS.length]
          return (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 18,
                padding: '18px 26px',
                borderRadius: 16,
                background: 'rgba(255,255,255,0.05)',
                border: `1.5px solid ${color}55`,
                boxShadow: `0 0 30px ${color}22`,
                opacity: s,
                transform: `translateX(${(1 - s) * -40}px)`,
              }}
            >
              <div style={{ width: 12, height: 12, borderRadius: '50%', background: color, boxShadow: `0 0 12px ${color}`, flexShrink: 0 }} />
              <div style={{ fontSize: 26, color: C.text, fontWeight: 600, lineHeight: 1.35 }}>{p}</div>
            </div>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 36 }}>
        {Array.from({ length: total }).map((_, i) => (
          <div key={i} style={{ width: i === index ? 22 : 8, height: 8, borderRadius: 4, background: i === index ? C.blue : '#33415588' }} />
        ))}
      </div>
    </AbsoluteFill>
  )
}

export const LectureVideo: React.FC<LectureVideoProps> = ({ title, scenes }) => {
  const data = scenes && scenes.length ? scenes : DEFAULT_SCENES
  const videoTitle = title || DEFAULT_TITLE
  return (
    <AbsoluteFill style={{ background: `linear-gradient(160deg, ${C.bg1}, ${C.bg2})`, fontFamily: 'system-ui, sans-serif' }}>
      {data.map((scene, i) => (
        <Sequence key={i} from={i * SCENE_FRAMES} durationInFrames={SCENE_FRAMES}>
          <SceneCard scene={scene} index={i} total={data.length} videoTitle={videoTitle} />
        </Sequence>
      ))}
    </AbsoluteFill>
  )
}
