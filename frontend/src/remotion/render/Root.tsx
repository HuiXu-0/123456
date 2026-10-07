/**
 * 服务端渲染专用合成（mp4 导出）。前端实时播放走 components/VideoLecture.tsx 的
 * @remotion/player + edge-tts；本合成仅用于 @remotion/renderer 把「画面 + 各段旁白」
 * 烧录成一体 mp4，与实时播放各司其职、互不影响。
 *
 * 与实时播放的关键差异 = 音画精确同步：
 * - 每个分镜场景的时长 = 该段 narration 经 edge-tts 合成的音频帧数（后端用 mutagen 量出
 *   时长 × fps，写入 scenes[].durationInFrames），而非固定 6s；
 * - 每段叠一条 <Audio>（音频以 data:URI 经 inputProps 传入，避免 staticFile/publicDir，
 *   从而 bundle 产物可缓存复用），renderMedia 自动把音轨混入同一 mp4。
 *
 * 画面复用 LectureVideo 导出的 SceneCard / 配色，保证 mp4 与实时预览观感一致。
 */
import { AbsoluteFill, Audio, Composition, Sequence } from 'remotion'
import { SceneCard, C, VIDEO_FPS, VIDEO_W, VIDEO_H, SCENE_FRAMES, type LectureScene } from '../LectureVideo'

/** 渲染用分镜场景：在 LectureScene 基础上带「本段帧时长 + 旁白音频 data:URI」。 */
export interface RenderScene extends LectureScene {
  /** 本段时长（帧）= 旁白音频时长 × fps（后端量出后写入）。 */
  durationInFrames: number
  /** 旁白音频 data:URI（data:audio/mpeg;base64,...）；为空则该段静音。 */
  audio?: string | null
}

export interface RenderProps {
  title: string
  scenes: RenderScene[]
}

/** 把各段按其音频帧数顺次铺帧，叠加同段 <Audio>，画面与旁白逐段对齐。 */
const LectureVideoRender: React.FC<RenderProps> = ({ title, scenes }) => {
  let from = 0
  return (
    <AbsoluteFill style={{ background: `linear-gradient(160deg, ${C.bg1}, ${C.bg2})`, fontFamily: 'system-ui, sans-serif' }}>
      {scenes.map((scene, i) => {
        const dur = Math.max(1, Math.round(scene.durationInFrames || SCENE_FRAMES))
        const start = from
        from += dur
        return (
          <Sequence key={i} from={start} durationInFrames={dur}>
            <SceneCard scene={scene} index={i} total={scenes.length} videoTitle={title} dur={dur} />
            {scene.audio ? <Audio src={scene.audio} /> : null}
          </Sequence>
        )
      })}
    </AbsoluteFill>
  )
}

const DEFAULT_PROPS: RenderProps = {
  title: '讲解视频',
  scenes: [{ title: '讲解视频', points: ['服务端渲染占位'], narration: '占位', durationInFrames: SCENE_FRAMES, audio: null }],
}

export const RemotionRoot: React.FC = () => (
  <Composition
    id="LectureRender"
    component={LectureVideoRender as React.FC}
    fps={VIDEO_FPS}
    width={VIDEO_W}
    height={VIDEO_H}
    defaultProps={DEFAULT_PROPS}
    /* 总时长由各段音频帧数累加得出（变长），无需固定时间轴；后端按音频量出每段帧数。 */
    calculateMetadata={({ props }) => {
      const scenes = (props as unknown as RenderProps).scenes ?? []
      const total = scenes.reduce((acc, s) => acc + Math.max(1, Math.round(s.durationInFrames || SCENE_FRAMES)), 0)
      return { durationInFrames: Math.max(1, total) }
    }}
  />
)
