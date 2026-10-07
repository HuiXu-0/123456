import { AbsoluteFill, Audio, Composition, Sequence, staticFile } from 'remotion'
import { LectureVideo, VIDEO_FPS, VIDEO_W, VIDEO_H, SCENE_FRAMES, DEFAULT_SCENES, DEFAULT_TITLE } from '../src/remotion/LectureVideo'

const probeScenes = DEFAULT_SCENES.slice(0, 1) // 1 个场景 = 180 帧 = 6s

// 画面复用现有 LectureVideo，叠加一条外部音轨（模拟 edge-tts 旁白）
const WithAudio: React.FC<{ title: string; scenes: typeof probeScenes }> = (p) => (
  <AbsoluteFill>
    <LectureVideo {...p} />
    {/* 旁白音轨从第 0 帧起播；多场景时每段一个 <Sequence from=...><Audio/></Sequence> */}
    <Sequence from={0}>
      <Audio src={staticFile('narration.wav')} />
    </Sequence>
  </AbsoluteFill>
)

export const RemotionRoot: React.FC = () => (
  <Composition
    id="LectureAudio"
    component={WithAudio as any}
    durationInFrames={SCENE_FRAMES}
    fps={VIDEO_FPS}
    width={VIDEO_W}
    height={VIDEO_H}
    defaultProps={{ title: DEFAULT_TITLE, scenes: probeScenes }}
  />
)
