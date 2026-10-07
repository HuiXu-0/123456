import { readFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'

const DIST_DIR = resolve('dist')
const ENTRY_LIMIT_BYTES = 400 * 1024
const HEAVY_PRELOAD_PATTERN = /vendor-(?:echarts|markdown|markmap|remotion|syntax)/

const html = await readFile(resolve(DIST_DIR, 'index.html'), 'utf8')
const entryMatch = html.match(/<script[^>]+src="([^"]+\/index-[^"]+\.js)"/)

if (!entryMatch) {
  throw new Error('未在 dist/index.html 中找到生产入口脚本')
}

const entryPath = resolve(DIST_DIR, entryMatch[1].replace(/^\//, ''))
const entryBytes = (await stat(entryPath)).size
const heavyPreloads = [...html.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="([^"]+)"/g)]
  .map((match) => match[1])
  .filter((href) => HEAVY_PRELOAD_PATTERN.test(href))

const failures = []
if (entryBytes > ENTRY_LIMIT_BYTES) {
  failures.push(`入口脚本 ${(entryBytes / 1024).toFixed(2)} KiB 超过 ${ENTRY_LIMIT_BYTES / 1024} KiB 预算`)
}
if (heavyPreloads.length > 0) {
  failures.push(`首屏预加载了重型依赖：${heavyPreloads.join(', ')}`)
}

if (failures.length > 0) {
  throw new Error(`Bundle 预算检查失败\n- ${failures.join('\n- ')}`)
}

console.log(
  `Bundle 预算检查通过：入口 ${(entryBytes / 1024).toFixed(2)} KiB；首屏重型 vendor preload 0 个`,
)
