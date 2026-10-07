// 一次性脚本：把 @fontsource 的 woff2 拷到 public/fonts，并生成 src/styles/fonts.css
// 原因：Vite dev 不会重写 @fontsource CSS 里的相对 url(./files/...)，改用 public/ 绝对路径最稳。
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, basename } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const nm = join(root, 'node_modules')
const fontsOut = join(root, 'public', 'fonts')
mkdirSync(fontsOut, { recursive: true })

// 每个字体：包路径 + 需要的 CSS（权重）
// 注：CJK 使用系统字体栈（微软雅黑/思源/PingFang），不自托管（Noto SC webfont 体积 75MB 且子集不全）。
// 这里只自托管拉丁标题字 Sora 与数据等宽字 JetBrains Mono。
const sources = [
  { pkg: '@fontsource-variable/sora', css: ['index.css'] },
  { pkg: '@fontsource/jetbrains-mono', css: ['400.css', '500.css'] },
]

let combined = `/* 自动生成 by scripts/build-fonts.mjs —— 自托管字体，离线可用。请勿手改。 */\n`
let copied = 0

for (const { pkg, css } of sources) {
  const pkgDir = join(nm, pkg)
  for (const cssFile of css) {
    let text = readFileSync(join(pkgDir, cssFile), 'utf8')
    // 只保留 woff2 的 src，丢弃 woff 回退；并把 ./files/ 重写为 /fonts/
    text = text.replace(/src:\s*([^;]+);/g, (_m, srcList) => {
      const woff2 = srcList
        .split(',')
        .map((s) => s.trim())
        .filter((s) => s.includes('.woff2'))
        .map((s) => s.replace(/url\(\.\/files\/([^)]+)\)/, (_mm, f) => `url(/fonts/${f})`))
      return `src: ${woff2.join(', ')};`
    })
    combined += `\n/* ${pkg} / ${cssFile} */\n` + text + '\n'

    // 拷贝该 CSS 引用到的所有 woff2 文件
    const refs = [...text.matchAll(/url\(\/fonts\/([^)]+\.woff2)\)/g)].map((m) => m[1])
    for (const f of refs) {
      const src = join(pkgDir, 'files', basename(f))
      const dst = join(fontsOut, basename(f))
      if (existsSync(src) && !existsSync(dst)) {
        copyFileSync(src, dst)
        copied++
      }
    }
  }
}

writeFileSync(join(root, 'src', 'styles', 'fonts.css'), combined, 'utf8')
console.log(`fonts.css 生成完成；拷贝 woff2 ${copied} 个 → public/fonts/`)
