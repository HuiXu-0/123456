import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { Player, type PlayerRef } from '@remotion/player'
import {
  LectureVideo,
  DEFAULT_SCENES,
  DEFAULT_TITLE,
  SCENE_FRAMES,
  VIDEO_FPS,
  VIDEO_W,
  VIDEO_H,
  type LectureScene,
} from '../remotion/LectureVideo'
import { USE_REAL_API } from '../services/api'
import { kpVideoScript } from '../data/kpResources'
import { getVideo, startVideoRender, getTaskStatus, type VideoNarrationLine } from '../services/resource'
import { getResourceKpId } from '../services/resourceNav'
import { ttsSpeak, ttsStop } from '../services/tts'
import { sanitizeFilename } from '../utils/lectureExport'
import './VideoLecture.css'

/* 由当前帧定位旁白段落索引 */
const segIndexAt = (frame: number, script: VideoNarrationLine[]) => {
  let idx = 0
  for (let i = 0; i < script.length; i++) if (frame >= script[i].from) idx = i
  return idx
}

/* 场景入场动画完成、淡出之前的稳定帧：作为暂停态静帧海报（帧 0 处场景 opacity≈0 会显空白）。 */
const SETTLE_FRAME = 36

/**
 * Remotion 讲解视频播放器（分镜脚本 → 参数化模板渲染 + 同步旁白/字幕）。
 *
 * 两种用法：
 * - 非受控（默认，主资源页讲解视频卡）：联调按当前知识点自取 8.3 分镜脚本，mock/失败回落默认；
 * - 受控（导学对话·按需生成视频）：外部直接传入已生成的 title/scenes，跳过自取，复用同一播放逻辑。
 */
export default function VideoLecture({
  title: propTitle,
  scenes: propScenes,
  difficulty = '初级',
  kpId: propKpId,
}: { title?: string; scenes?: LectureScene[]; difficulty?: string; kpId?: string } = {}) {
  /* 知识点：显式传入（如「我的资源库」预览指定 kp）优先，否则取资源页粘性 kpId（原行为不变）。 */
  const resolvedKpId = propKpId ?? getResourceKpId()
  const playerRef = useRef<PlayerRef>(null)
  const [seg, setSeg] = useState(0)
  const [narration, setNarration] = useState(true)
  const [playing, setPlaying] = useState(false)
  /* 这些镜像 ref 供播放器事件回调（闭包仅在挂载时绑定一次）读到最新值，避免重绑监听。 */
  const segRef = useRef(0)
  const narrationRef = useRef(narration)
  const playingRef = useRef(false)
  /* 当前“正在朗读”的段索引（-1=空闲）：防止 play 事件重复触发、把同一段反复重念。
     仅在暂停/停止时复位为 -1，从而“继续/跳段”可重念，正常推进各段各念一次。 */
  const speakingSegRef = useRef(-1)

  /* 受控：外部已提供分镜脚本（如导学对话按需生成），直接渲染、不再自取。 */
  const controlled = !!propScenes?.length

  /* B7-b 联调 + 动态分镜：非受控时分镜脚本（标题/要点/旁白）改吃 8.3 POST /resource/video，
     画面与旁白随当前知识点动态生成；mock 模式按 kpId 取本地分镜库（kpVideoScript：
     5 核心点精修 + 体系点通用模板；nn/未知返回 null → 内置 nn 默认脚本，零回归）；
     联调请求失败同样回落本地分镜，朗读与点击 seek 行为不变 */
  const [fetchedScenes, setFetchedScenes] = useState<LectureScene[]>(
    () => kpVideoScript(resolvedKpId)?.scenes ?? DEFAULT_SCENES
  )
  const [fetchedTitle, setFetchedTitle] = useState<string>(() => kpVideoScript(resolvedKpId)?.title ?? DEFAULT_TITLE)
  useEffect(() => {
    if (controlled) return
    if (!USE_REAL_API) {
      // mock：按当前知识点选本地分镜（组件通常随页面重挂载，此处兜底 kpId 变化不重挂的场景）
      const local = kpVideoScript(resolvedKpId)
      setFetchedScenes(local?.scenes ?? DEFAULT_SCENES)
      setFetchedTitle(local?.title ?? DEFAULT_TITLE)
      return
    }
    // 难度跟随当前实际选择（与讲义/缓存键 (kp_id, difficulty, kind) 一致；切档自动重取）
    getVideo(resolvedKpId, difficulty)
      .then((d) => {
        if (d.scenes?.length) {
          setFetchedScenes(d.scenes.map((s) => ({ title: s.title, points: s.points, narration: s.narration })))
          setFetchedTitle(d.title || DEFAULT_TITLE)
        }
      })
      .catch((e) => console.error('[video] 加载分镜脚本失败，使用本地默认脚本', e))
  }, [controlled, difficulty, resolvedKpId])

  /* 服务端渲染 mp4（增强项）：拿到一体 mp4（画面+旁白已烧录）→ 用原生 <video> 播放/下载；
     渲染中显示轮询态；无渲染能力 / 失败 / 超时 / mock / 受控生成 → 保持下方实时 Player+TTS 兜底，
     绝不破坏「拿不到 mp4 也能看」。 */
  const [mp4Url, setMp4Url] = useState<string | null>(null)
  const [rendering, setRendering] = useState(false)
  /* 渲染进度 0-100（来自任务 progress）：渲染中显示「正在生成真视频… X%」 */
  const [renderProgress, setRenderProgress] = useState(0)
  useEffect(() => {
    if (controlled || !USE_REAL_API) return
    let cancelled = false
    let timer: number | undefined
    // 难度切换 → 重新对齐缓存键：清掉上一档 mp4/进度，避免串档（如高级讲义却放初级视频）
    setMp4Url(null)
    setRendering(false)
    setRenderProgress(0)
    const kp = resolvedKpId
    const poll = (taskId: string, tries: number) => {
      timer = window.setTimeout(() => {
        getTaskStatus<{ videoUrl: string }>(taskId)
          .then((t) => {
            if (cancelled) return
            if (typeof t.progress === 'number') setRenderProgress(t.progress)
            if (t.status === 'succeeded' && t.result?.videoUrl) {
              setRenderProgress(100)
              setMp4Url(t.result.videoUrl)
              setRendering(false)
            } else if (t.status === 'failed' || tries <= 0) {
              setRendering(false) // 失败/超时 → 回落实时 Player
            } else {
              poll(taskId, tries - 1)
            }
          })
          .catch(() => {
            if (!cancelled) setRendering(false)
          })
      }, 2000)
    }
    startVideoRender(kp, difficulty)
      .then((r) => {
        if (cancelled) return
        if (r.status === 'ready' && r.videoUrl) setMp4Url(r.videoUrl)
        else if (r.status === 'rendering' && r.taskId) {
          setRendering(true)
          setRenderProgress(5)
          poll(r.taskId, 90) // 最长 ~3min 轮询，超时回落
        }
        // unavailable → 保持实时 Player 兜底
      })
      .catch(() => {
        /* 渲染触发失败 → 保持实时 Player 兜底 */
      })
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [controlled, difficulty, resolvedKpId])

  const scenes = controlled ? propScenes! : fetchedScenes
  const title = controlled ? propTitle || DEFAULT_TITLE : fetchedTitle

  /* 旁白脚本（侧栏字幕 + TTS）由分镜场景派生：场景 i 起始帧 = i × SCENE_FRAMES */
  const script = useMemo<VideoNarrationLine[]>(
    () => scenes.map((s, i) => ({ from: i * SCENE_FRAMES, text: s.narration })),
    [scenes]
  )
  const scriptRef = useRef(script)
  scriptRef.current = script
  const total = script.length
  const durationInFrames = scenes.length * SCENE_FRAMES
  /* 稳定 inputProps 引用：避免每次 setState 重渲染都生成新对象，触发 Player 重置 / 事件重发。 */
  const inputProps = useMemo(() => ({ title, scenes }), [title, scenes])

  useEffect(() => {
    narrationRef.current = narration
  }, [narration])

  /* —— 路线 A：语音驱动场景推进 ——
     场景不再由 Remotion 固定时间轴翻页，而是「当前段旁白念完 → 才推进下一段」。
     speakSeg 朗读第 i 段，onEnd（音频/utterance 自然结束）触发 handleNarrationEnd 推进。
     用 speakSegRef 打破 speakSeg ↔ handleNarrationEnd 的相互引用。 */
  const speakSegRef = useRef<(i: number) => void>(() => {})

  /* 暂停：唯一“停”入口（用户点暂停 / 末段念完 / 卸载）。重置朗读态并停声。
     关键：narration 模式下播放推进**不依赖** Remotion 的 play/pause 事件——因为
     onFrame 里的 seekTo 循环会让 Player 反复发出 pause→play，若据此驱动朗读会形成死循环。 */
  const doPause = useCallback(() => {
    playingRef.current = false
    setPlaying(false)
    speakingSegRef.current = -1
    playerRef.current?.pause()
    ttsStop()
  }, [])

  const handleNarrationEnd = useCallback(
    (i: number) => {
      // 仅当仍在播放、旁白开启、且 i 仍是当前段时推进（防过期回调误推进）
      if (!playingRef.current || !narrationRef.current) return
      if (i !== segRef.current) return
      const last = scriptRef.current.length - 1
      if (i < last) {
        const next = i + 1
        segRef.current = next
        setSeg(next)
        playerRef.current?.seekTo(next * SCENE_FRAMES) // 画面切到下一段（持续播放，由 onFrame 夹在段内）
        speakSegRef.current(next)
      } else {
        doPause() // 末段念完 → 停
      }
    },
    [doPause]
  )

  const speakSeg = useCallback(
    (i: number) => {
      const line = scriptRef.current[i]
      if (!line) return
      speakingSegRef.current = i
      void ttsSpeak(line.text, { onEnd: () => handleNarrationEnd(i) })
    },
    [handleNarrationEnd]
  )
  useEffect(() => {
    speakSegRef.current = speakSeg
  }, [speakSeg])

  const doPlay = useCallback(() => {
    playingRef.current = true
    setPlaying(true)
    playerRef.current?.play()
    // 旁白开启且当前段未在朗读 → 朗读当前段（推进由 onEnd 链式驱动，非 Player 事件）
    if (narrationRef.current && speakingSegRef.current !== segRef.current) {
      speakSegRef.current(segRef.current)
    }
  }, [])

  useEffect(() => {
    const p = playerRef.current
    if (!p) return
    const onFrame = (e: { detail: { frame: number } }) => {
      const f = e.detail.frame
      if (narrationRef.current) {
        // 语音驱动：把画面夹在「当前段」帧窗内循环，绝不因时间轴提前切段、打断语音。
        // 用 -1 作上界：末段窗口末帧 = durationInFrames-1，否则末段永不触发循环、
        // 会跑到真正结尾触发 ended，把最后一段语音（常长于 6s）截断。
        const start = segRef.current * SCENE_FRAMES
        if (f >= start + SCENE_FRAMES - 1 || f < start) p.seekTo(start)
      } else {
        // 无旁白：回退到时间轴驱动，高亮跟随帧（保留原行为）
        const idx = segIndexAt(f, scriptRef.current)
        if (idx !== segRef.current) {
          segRef.current = idx
          setSeg(idx)
        }
      }
    }
    // ended 仅在“无旁白·时间轴跑到底”时收尾；旁白模式靠 onFrame 夹住、永不到底，
    // 即便触发也忽略（推进交给语音 onEnd），避免末段语音被时间轴结尾截断
    const onEnded = () => {
      if (!narrationRef.current) doPause()
    }
    p.addEventListener('frameupdate', onFrame)
    p.addEventListener('ended', onEnded)
    return () => {
      p.removeEventListener('frameupdate', onFrame)
      p.removeEventListener('ended', onEnded)
      ttsStop() // 卸载时停掉当前音频，避免串音
    }
  }, [doPause])

  /* 当前旁白段（脚本切换瞬间防越界兜底） */
  const curLine = script[seg] ?? script[script.length - 1]

  /* 自定义播放控制（取代 Remotion 原生时间轴：原生进度条无法表达「语音驱动、段时长不等」）。 */
  const togglePlay = useCallback(() => {
    if (playingRef.current) doPause()
    else doPlay()
  }, [doPause, doPlay])

  /* 点击旁白行：跳到该段并从该段开始朗读（保留原“点击 seek”能力，改为段粒度）。 */
  const jumpToScene = useCallback((i: number) => {
    const p = playerRef.current
    if (!p) return
    segRef.current = i
    setSeg(i)
    speakingSegRef.current = -1 // 允许对目标段重念
    p.seekTo(i * SCENE_FRAMES)
    if (playingRef.current) {
      if (narrationRef.current) speakSegRef.current(i)
    } else {
      doPlay() // 未播放 → 起播并朗读当前段
    }
  }, [doPlay])

  const toggleNarration = useCallback((on: boolean) => {
    setNarration(on)
    narrationRef.current = on
    if (!on) {
      speakingSegRef.current = -1
      ttsStop() // 关旁白 → 停声（画面回到时间轴驱动）
    } else if (playingRef.current) {
      speakSegRef.current(segRef.current) // 开旁白 → 朗读当前段
    }
  }, [])

  /* —— mp4 就绪：画面+旁白已烧录一体，用原生 <video> 播放并可下载（不再 Player+外挂 TTS） —— */
  if (mp4Url) {
    return (
      <div className="video-lecture">
        <div className="video-lecture__player">
          <video
            src={mp4Url}
            controls
            className="video-lecture__mp4"
            style={{ width: '100%', borderRadius: 16, overflow: 'hidden', boxShadow: 'var(--shadow-glass)', background: '#0a1024' }}
          />
          <div className="video-lecture__controls">
            <a className="video-lecture__download" href={mp4Url} download={`讲解视频-${sanitizeFilename(title)}.mp4`}>
              <span aria-hidden="true">⬇</span> 下载 mp4
            </a>
            <span className="video-lecture__seg-count">画面 + 旁白已烧录一体 · 真视频</span>
          </div>
        </div>
        <div className="video-lecture__side">
          <div className="video-lecture__side-head">
            <span>🎙️ AI 旁白脚本</span>
          </div>
          <ol className="video-lecture__script">
            {script.map((n, i) => (
              <li key={i} className="video-lecture__line">
                <span className="video-lecture__ts">{String(Math.floor(n.from / VIDEO_FPS)).padStart(2, '0')}s</span>
                {n.text}
              </li>
            ))}
          </ol>
          <p className="video-lecture__note">
            该视频由 Remotion 服务端渲染、edge-tts 旁白已混音烧录为一体 mp4（h264 + aac），可直接播放与下载，无需外挂 TTS。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="video-lecture">
      <div className="video-lecture__player">
        {rendering && (
          <div className="video-lecture__rendering" role="status" aria-live="polite">
            <span className="video-lecture__rendering-orb" />
            <div className="video-lecture__rendering-body">
              <strong className="video-lecture__rendering-title">正在生成真视频…</strong>
              <span className="video-lecture__rendering-sub">
                画面 + 旁白烧录为一体 mp4，完成后自动切换 · 下方为实时预览，可先看
              </span>
              <div className="video-lecture__rendering-bar" aria-hidden>
                <div
                  className="video-lecture__rendering-fill"
                  style={{ width: `${Math.min(100, Math.max(5, renderProgress))}%` }}
                />
              </div>
            </div>
            <span className="video-lecture__rendering-pct">{Math.min(100, Math.max(5, renderProgress))}%</span>
          </div>
        )}
        <Player
          ref={playerRef}
          component={LectureVideo}
          inputProps={inputProps}
          durationInFrames={durationInFrames}
          fps={VIDEO_FPS}
          compositionWidth={VIDEO_W}
          compositionHeight={VIDEO_H}
          initialFrame={SETTLE_FRAME}
          clickToPlay={false}
          style={{ width: '100%', borderRadius: 16, overflow: 'hidden', boxShadow: 'var(--shadow-glass)' }}
        />
        {/* 自定义控制条：语音驱动下用「段进度」表达，而非固定时间轴 */}
        <div className="video-lecture__controls">
          <button
            type="button"
            className="video-lecture__playbtn"
            onClick={togglePlay}
            aria-label={playing ? '暂停' : '播放'}
          >
            {playing ? '⏸' : '▶'}
          </button>
          <div className="video-lecture__progress" aria-hidden>
            <div
              className="video-lecture__progress-fill"
              style={{ width: `${(((playing || seg > 0 ? seg + 1 : 0)) / total) * 100}%` }}
            />
          </div>
          <span className="video-lecture__seg-count">
            第 {seg + 1} / {total} 段
          </span>
        </div>
        {/* 字幕 */}
        <div className="video-lecture__caption">{curLine.text}</div>
      </div>

      {/* 旁白脚本 + TTS 控制 */}
      <div className="video-lecture__side">
        <div className="video-lecture__side-head">
          <span>🎙️ AI 旁白脚本</span>
          <label className="video-lecture__toggle">
            <input
              type="checkbox"
              checked={narration}
              onChange={(e) => toggleNarration(e.target.checked)}
            />
            语音朗读
          </label>
        </div>
        <ol className="video-lecture__script">
          {script.map((n, i) => (
            <li
              key={i}
              className={`video-lecture__line ${i === seg ? 'video-lecture__line--active' : ''}`}
              onClick={() => jumpToScene(i)}
            >
              <span className="video-lecture__ts">{String(Math.floor(n.from / VIDEO_FPS)).padStart(2, '0')}s</span>
              {n.text}
            </li>
          ))}
        </ol>
        <p className="video-lecture__note">
          视频由 Remotion（React 代码渲染）生成；旁白接入后端 edge-tts 自然语音（失败自动回落浏览器语音），生产环境可服务端渲染导出 MP4。
        </p>
      </div>
    </div>
  )
}
