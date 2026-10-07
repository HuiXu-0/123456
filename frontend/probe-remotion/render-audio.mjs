import { bundle } from '@remotion/bundler'
import { renderMedia, selectComposition } from '@remotion/renderer'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const out = path.join(here, 'out-audio.mp4')
const browserExecutable =
  'C:/Users/力恺/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'

const t0 = Date.now()
const serveUrl = await bundle({
  entryPoint: path.join(here, 'index-audio.ts'),
  publicDir: here, // narration.wav 作为 staticFile 来源
})
console.log(`[probe] bundle ${((Date.now() - t0) / 1000).toFixed(1)}s`)
const composition = await selectComposition({ serveUrl, id: 'LectureAudio', browserExecutable })
const tR = Date.now()
await renderMedia({ composition, serveUrl, codec: 'h264', browserExecutable, outputLocation: out })
console.log(`[probe] render ${((Date.now() - tR) / 1000).toFixed(1)}s -> ${out}`)
