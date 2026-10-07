import { bundle } from '@remotion/bundler'
import { renderMedia, selectComposition } from '@remotion/renderer'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const entry = path.join(here, 'index.ts')
const out = path.join(here, 'out.mp4')

// 复用已存在的 Playwright headless shell，绕过 Remotion 从 googleapis 的慢下载
const browserExecutable =
  'C:/Users/力恺/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'

const t0 = Date.now()
console.log('[probe] bundling...')
const serveUrl = await bundle({
  entryPoint: entry,
  // 默认 webpack 配置即可处理 tsx；不覆盖
})
const tBundle = Date.now()
console.log(`[probe] bundle done in ${((tBundle - t0) / 1000).toFixed(1)}s`)

const composition = await selectComposition({ serveUrl, id: 'Lecture', browserExecutable })
console.log(`[probe] composition: ${composition.width}x${composition.height} ${composition.fps}fps ${composition.durationInFrames}f`)

await renderMedia({
  composition,
  serveUrl,
  codec: 'h264',
  browserExecutable,
  outputLocation: out,
  onProgress: ({ progress }) => {
    if (Math.round(progress * 100) % 20 === 0) process.stdout.write(`  render ${Math.round(progress * 100)}%\r`)
  },
})
const tEnd = Date.now()
console.log(`\n[probe] render done in ${((tEnd - tBundle) / 1000).toFixed(1)}s`)
console.log(`[probe] TOTAL ${((tEnd - t0) / 1000).toFixed(1)}s -> ${out}`)
