import { useEffect, useState } from 'react'
import './ThemeToggle.css'

type Theme = 'sage' | 'ink'

const KEY = 'zx-theme'

function apply(theme: Theme) {
  // 鼠尾草 = 默认（:root），墨纸 = data-theme='ink'
  if (theme === 'ink') document.documentElement.setAttribute('data-theme', 'ink')
  else document.documentElement.removeAttribute('data-theme')
}

/** 护眼主题切换：鼠尾草 Sage（默认）↔ 墨纸 Ink-Paper。仅换配色，不影响动画/布局/逻辑。*/
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem(KEY)
    return saved === 'ink' ? 'ink' : 'sage'
  })

  useEffect(() => {
    apply(theme)
    localStorage.setItem(KEY, theme)
  }, [theme])

  const ink = theme === 'ink'

  return (
    <button
      className={`theme-toggle ${ink ? 'theme-toggle--ink' : ''}`}
      onClick={() => setTheme(ink ? 'sage' : 'ink')}
      title="切换护眼主题"
      aria-label="切换护眼主题"
    >
      <span className="theme-toggle__dot" />
      <span className="theme-toggle__label">{ink ? '墨纸' : '鼠尾草'}</span>
      <span className="theme-toggle__switch">
        <span className="theme-toggle__knob" />
      </span>
    </button>
  )
}
