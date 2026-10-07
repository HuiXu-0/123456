import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ThemeToggle from './components/ThemeToggle'

// 自托管字体（离线可用，不依赖 Google Fonts CDN）
// woff2 位于 public/fonts/，由 scripts/build-fonts.mjs 生成 fonts.css（绝对 /fonts/ 路径，规避 Vite 对 @fontsource 相对路径不重写的问题）
// 拉丁标题: Sora Variable | 数据等宽: JetBrains Mono | 中文: 系统字体栈（见 globals.css）
import './styles/fonts.css'
import './styles/globals.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
    {/* 全局风格切换浮动按钮（固定定位，跨 landing/login/app 三段均可见，不参与布局）*/}
    <ThemeToggle />
  </React.StrictMode>,
)