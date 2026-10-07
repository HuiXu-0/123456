/**
 * 讲解视频服务端渲染驱动（Node）。被后端 app/services/video_render.py 经 subprocess 调用。
 *
 * 用法：node scripts/render-lecture.mjs <configJson>
 *   configJson 是一个 JSON 文件路径，结构见下方 cfg 解构。后端在其中传入：
 *   分镜（含各段 durationInFrames + 旁白音频 data:URI）、输出 mp4 路径、bundle 缓存目录、
 *   无头浏览器可执行文件路径。
 *
 * 关键点（沿用上一步探测确认的方案）：
 * - bundle 产物缓存复用：bundle 一次落到 bundleDir，源未变则后续直接以 bundleDir 作
 *   serveUrl 跳过 bundle（webpack 持久缓存亦在）；源（src/remotion）较 bundle 新则重建；
 * - browserExecutable 固定指向复用的 Playwright chrome-headless-shell，规避 googleapis 下载；
 * - <Audio>（data:URI）经 inputProps 传入，renderMedia 自动混音进同一 mp4，无需系统/后期 ffmpeg。
 *
 * 输出：stdout 末行打印 `RENDER_OK <seconds>`（成功）或 `RENDER_ERR <msg>`（失败），exit code 同步。
 */
import { bundle } from '@remotion/bundler'
import { renderMedia, selectComposition } from '@remotion/renderer'
import { existsSync, readFileSync, statSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(here, '..')

function fail(msg) {
  console.log(`RENDER_ERR ${msg}`)
  process.exit(1)
}

const cfgPath = process.argv[2]
if (!cfgPath || !existsSync(cfgPath)) fail(`config json not found: ${cfgPath}`)

/** cfg = { title, scenes:[{title,points,narration,durationInFrames,audio}], fps, width, height, outputPath, bundleDir, browserExecutable } */
const cfg = JSON.parse(readFileSync(cfgPath, 'utf-8'))
const entryPoint = path.join(frontendRoot, 'src', 'remotion', 'render', 'index.ts')

/** 取目录树下最新修改时间（毫秒）。用于判断 bundle 是否相对源码过期需重建。 */
function newestMtime(dir) {
  let newest = 0
  const walk = (d) => {
    for (const name of readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, name.name)
      if (name.isDirectory()) walk(p)
      else newest = Math.max(newest, statSync(p).mtimeMs)
    }
  }
  try {
    walk(dir)
  } catch {
    /* 目录不存在 → 0 */
  }
  return newest
}

async function main() {
  const t0 = Date.now()
  const bundleDir = cfg.bundleDir
  const bundleMarker = bundleDir ? path.join(bundleDir, 'index.html') : null

  // bundle 缓存复用：已有产物且不比源码旧 → 直接复用，跳过 bundle（实现「只 bundle 一次」）。
  const srcDir = path.join(frontendRoot, 'src', 'remotion')
  let serveUrl
  if (bundleMarker && existsSync(bundleMarker) && statSync(bundleMarker).mtimeMs >= newestMtime(srcDir)) {
    serveUrl = bundleDir
    console.log(`[render] reuse bundle ${bundleDir}`)
  } else {
    serveUrl = await bundle({ entryPoint, outDir: bundleDir })
    console.log(`[render] bundle ${((Date.now() - t0) / 1000).toFixed(1)}s -> ${serveUrl}`)
  }

  const inputProps = { title: cfg.title, scenes: cfg.scenes }
  const composition = await selectComposition({
    serveUrl,
    id: 'LectureRender',
    inputProps,
    browserExecutable: cfg.browserExecutable || undefined,
  })

  const tR = Date.now()
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    inputProps,
    browserExecutable: cfg.browserExecutable || undefined,
    outputLocation: cfg.outputPath,
  })
  const secs = ((Date.now() - t0) / 1000).toFixed(1)
  console.log(`[render] renderMedia ${((Date.now() - tR) / 1000).toFixed(1)}s -> ${cfg.outputPath}`)
  console.log(`RENDER_OK ${secs}`)
}

main().catch((e) => fail(String(e?.stack || e?.message || e)))
