import { Composition } from 'remotion'
import {
  LectureVideo,
  VIDEO_FPS,
  VIDEO_W,
  VIDEO_H,
  SCENE_FRAMES,
  DEFAULT_SCENES,
  DEFAULT_TITLE,
} from '../src/remotion/LectureVideo'

// 探测用：只取前 2 个场景（最小样例）
const probeScenes = DEFAULT_SCENES.slice(0, 2)

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Lecture"
    component={LectureVideo as any}
    durationInFrames={probeScenes.length * SCENE_FRAMES}
    fps={VIDEO_FPS}
    width={VIDEO_W}
    height={VIDEO_H}
    defaultProps={{ title: DEFAULT_TITLE, scenes: probeScenes }}
  />
)
