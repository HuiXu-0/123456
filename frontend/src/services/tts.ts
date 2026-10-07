/**
 * 语音合成数据获取层（接口文档 §8.9 POST /tts/synthesize）。
 *
 * 把"发声引擎"封装为单一入口 {@link ttsSpeak}，对组件屏蔽后端细节：
 * - 成功（`Content-Type: audio/mpeg`）：blob → `new Audio(objectURL)` 播放，播完/出错
 *   即 `revokeObjectURL` 释放；
 * - 降级（`application/json`，信封 `code 2002 / offline=true`）：回落浏览器 speechSynthesis；
 * - 网络/异常 / 未联调（`USE_REAL_API=false`）：同样回落浏览器语音——**保证永远有声**。
 *
 * 全局只维持「一个」正在播放的音频：每次 {@link ttsSpeak} 先停掉当前再起新的，避免串音；
 * 用自增 seq 作令牌，fetch 期间若被新调用或 {@link ttsStop} 取代，则放弃本次播放。
 */
import { USE_REAL_API, getToken } from './api'

let currentAudio: HTMLAudioElement | null = null
let currentUrl: string | null = null
/** 播放令牌：每次 speak/stop 自增；异步回来后 seq 不一致即说明已被取代，放弃。 */
let seq = 0

/** 停掉当前音频 + 取消浏览器朗读 + 释放 objectURL（内部用，不动 seq）。 */
function stopInternal(): void {
  if (currentAudio) {
    currentAudio.pause()
    currentAudio.src = ''
    currentAudio = null
  }
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl)
    currentUrl = null
  }
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel()
  }
}

/** 浏览器原生 TTS 回落（与原 VideoLecture.speak 同口径：zh-CN、语速 1.05）。
 *  onEnd 在朗读自然结束 / 出错时回调（驱动“念完再推进”）；无 TTS 能力时立即回调，避免卡死。 */
function browserSpeak(text: string, onEnd?: () => void): void {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
    onEnd?.()
    return
  }
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'zh-CN'
  u.rate = 1.05
  if (onEnd) {
    u.onend = () => onEnd()
    u.onerror = () => onEnd() // cancel/中断也回调；调用方用 seq 守卫过滤被取代的旧段
  }
  window.speechSynthesis.speak(u)
}

/** 停止当前朗读（组件卸载 / 暂停 / 切换场景 / 关闭语音开关时调用），并使在途请求失效。 */
export function ttsStop(): void {
  seq++
  stopInternal()
}

/** {@link ttsSpeak} 选项：onEnd 在本段语音**自然念完**（或回落语音念完）时回调，
 *  供调用方实现“一段念完再推进下一段”（路线 A 语音驱动）。被 {@link ttsStop} /
 *  新的 speak 取代的旧段不会触发 onEnd（内部用 seq 守卫过滤）。 */
export interface TtsSpeakOptions {
  onEnd?: () => void
}

/**
 * 朗读一段文本。优先后端 edge-tts 自然语音，失败/降级回落浏览器语音。
 * 会先停掉当前正在播放的内容再开始本段，避免串音；本段念完触发 onEnd。
 */
export async function ttsSpeak(text: string, opts: TtsSpeakOptions = {}): Promise<void> {
  const content = (text || '').trim()
  const mySeq = ++seq
  stopInternal()

  // onEnd 守卫：仅当本段仍是“当前段”（未被新 speak / stop 取代）时才回调，防止误推进。
  const safeEnd = () => {
    if (mySeq === seq) opts.onEnd?.()
  }

  if (!content) {
    safeEnd() // 空文本：直接视为念完，链路继续推进，避免卡死
    return
  }

  // 未联调真实后端时直接用浏览器语音，避免无谓的失败请求（与 mock 模式行为一致）。
  if (!USE_REAL_API) {
    browserSpeak(content, safeEnd)
    return
  }

  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`

    const res = await fetch('/api/v1/tts/synthesize', {
      method: 'POST',
      headers,
      body: JSON.stringify({ text: content }),
    })
    if (mySeq !== seq) return // 在途期间已被新调用/停止取代

    const contentType = res.headers.get('Content-Type') || ''
    if (res.ok && contentType.includes('audio/mpeg')) {
      const blob = await res.blob()
      if (mySeq !== seq) return // blob 期间又被取代

      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      currentAudio = audio
      currentUrl = url
      const cleanup = () => {
        if (currentUrl === url) {
          URL.revokeObjectURL(url)
          currentUrl = null
        }
        if (currentAudio === audio) currentAudio = null
      }
      audio.addEventListener('ended', () => {
        cleanup()
        safeEnd() // 自然念完 → 推进下一段
      })
      audio.addEventListener('error', () => {
        cleanup()
        if (mySeq === seq) browserSpeak(content, safeEnd) // 播放出错 → 回落浏览器语音（仍会 onEnd）
      })
      try {
        await audio.play()
      } catch {
        cleanup()
        if (mySeq === seq) browserSpeak(content, safeEnd) // 自动播放被拦 → 回落浏览器语音
      }
      return
    }

    // 降级信封（offline=true）或非音频响应 → 回落浏览器语音
    browserSpeak(content, safeEnd)
  } catch {
    if (mySeq === seq) browserSpeak(content, safeEnd) // 网络/异常 → 回落，保证有声
  }
}
