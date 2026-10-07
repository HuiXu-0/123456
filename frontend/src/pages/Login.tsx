import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import Silk from '@/components/Silk/Silk'
import { USE_REAL_API, ApiError } from '../services/api'
import { login, register } from '../services/auth'
import { useJourney } from '../store/journey'
import { useMastery } from '../store/mastery'
import './Login.css'

interface LoginProps {
  /** 登录/注册成功回调；注册新学习者时 toProfile=true → 进入画像诊断引导（B9） */
  onLogin: (opts?: { toProfile?: boolean }) => void
}

type AuthMode = 'login' | 'register'

/* 绸缎背景的两套护眼配色：鼠尾草 → 低饱和鼠尾绿；墨纸 → 低饱和靛蓝灰 */
const SILK_COLORS = {
  sage: '#9FB3A6',
  ink: '#9DB1BC',
} as const

/** 观察 documentElement 的 data-theme，登录页随全局护眼主题实时联动 */
function useThemeName(): keyof typeof SILK_COLORS {
  const read = (): keyof typeof SILK_COLORS =>
    typeof document !== 'undefined' &&
    document.documentElement.getAttribute('data-theme') === 'ink'
      ? 'ink'
      : 'sage'

  const [theme, setTheme] = useState<keyof typeof SILK_COLORS>(read)

  useEffect(() => {
    const el = document.documentElement
    const obs = new MutationObserver(() => setTheme(read()))
    obs.observe(el, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [])

  return theme
}

const features = [
  { icon: '◆', text: '多智能体协同 · 学情诊断' },
  { icon: '◇', text: 'RAG 检索增强 · 抑制幻觉' },
  { icon: '◈', text: '个性化学习路径动态规划' },
]

export default function Login({ onLogin }: LoginProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [username, setUsername] = useState<string>('')
  const [password, setPassword] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const theme = useThemeName()
  const isRegister = mode === 'register'

  /** 切换登录/注册：两种模式均以空表单起步，由用户自行输入（零新设计语言，复用同表单） */
  const switchMode = (next: AuthMode) => {
    if (loading) return
    setMode(next)
    setUsername('')
    setPassword('')
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (loading) return
    // 客户端前置校验（即时反馈，避免无谓请求）；重名等仍以后端为权威（返回 code 1001）。
    // 注册要求：用户名非空 + 密码至少 6 位（与后端密码规则一致），不满足即拒并提示。
    const uname = username.trim()
    if (isRegister) {
      if (!uname) {
        window.alert('请填写用户名')
        return
      }
      if (password.length < 6) {
        window.alert('密码至少 6 位，请重新设置')
        return
      }
    }
    setLoading(true)
    // mock 模式：模拟鉴权（前端演示），1s 后进入系统（注册 → 进入画像诊断引导）
    if (!USE_REAL_API) {
      setTimeout(() => onLogin(isRegister ? { toProfile: true } : undefined), 1000)
      return
    }
    // 联调模式：真实登录/注册 → 落 token → 拉取旅程 / 掌握度权威态 → 进入系统
    try {
      if (isRegister) {
        await register(username.trim(), password)
        await Promise.all([useJourney.getState().loadJourney(), useMastery.getState().load()])
        onLogin({ toProfile: true }) // 新学习者直达画像诊断
      } else {
        await login(username.trim(), password)
        await Promise.all([useJourney.getState().loadJourney(), useMastery.getState().load()])
        onLogin()
      }
    } catch (err) {
      setLoading(false)
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : '请检查输入'
      window.alert((isRegister ? '注册失败：' : '登录失败：') + msg)
    }
  }

  return (
    <div className="login">
      {/* 背景：Silk 丝绸波纹（WebGL）+ 暗化遮罩 */}
      <div className="login__bg">
        <div className="login__silk">
          <Silk speed={8} scale={1.1} color={SILK_COLORS[theme]} noiseIntensity={0.9} rotation={0.15} />
        </div>
        <div className="login__silk-scrim" />
      </div>

      <motion.div
        className="login__panel"
        initial={{ opacity: 0, y: 24, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.6, ease: [0.25, 0.8, 0.25, 1] }}
      >
        {/* 左：品牌展示（talizen 风）*/}
        <div className="login__brand">
          {/* 背景巨字虚影 */}
          <span className="login__ghost" aria-hidden="true">AI</span>

          <motion.div
            className="login__logo"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.2, duration: 0.5 }}
          >
            <img src="/logo/logo-full-tagline.png" alt="智学中枢 · 多智能体协同 · 科学学习管家" />
          </motion.div>

          {/* talizen 签名：选择性渐变大标题 */}
          <motion.h1
            className="login__title"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.32, duration: 0.6, ease: [0.25, 0.8, 0.25, 1] }}
          >
            诊断<span className="grad">学情</span>。<br />
            生成<span className="grad">每一课</span>。
          </motion.h1>
          <motion.p
            className="login__subtitle"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.6 }}
          >
            AI Learning Operating System
          </motion.p>

          <motion.ul
            className="login__features"
            initial="hidden"
            animate="visible"
            variants={{ visible: { transition: { staggerChildren: 0.12, delayChildren: 0.8 } } }}
          >
            {features.map((f) => (
              <motion.li
                key={f.text}
                variants={{ hidden: { opacity: 0, x: -12 }, visible: { opacity: 1, x: 0 } }}
              >
                <span className="login__feature-icon">{f.icon}</span>
                {f.text}
              </motion.li>
            ))}
          </motion.ul>
        </div>

        {/* 右：登录表单 */}
        <motion.div
          className="login__form-wrap"
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.4, duration: 0.5 }}
        >
          <h2 className="login__form-title">{isRegister ? '创建账号' : '欢迎回来'}</h2>
          <p className="login__form-hint">
            {isRegister ? '注册一个学习者账号，立即开始画像诊断' : '登录以进入你的个性化学习空间'}
          </p>

          <form className="login__form" onSubmit={handleSubmit}>
            <label className="login__field">
              <span className="login__field-label">用户名</span>
              <input
                className="login__input"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                placeholder={isRegister ? '设置一个用户名' : '请输入用户名'}
              />
            </label>

            <label className="login__field">
              <span className="login__field-label">密码</span>
              <input
                className="login__input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                placeholder={isRegister ? '至少 6 位' : '请输入密码'}
              />
            </label>

            {!isRegister && (
              <div className="login__row">
                <label className="login__remember">
                  <input type="checkbox" defaultChecked /> 记住我
                </label>
                <a className="login__forgot" href="#">忘记密码？</a>
              </div>
            )}

            <button className={`login__submit ${loading ? 'login__submit--loading' : ''}`} type="submit">
              {loading ? (isRegister ? '正在注册…' : '正在进入…') : isRegister ? '注册并进入' : '进入系统'}
            </button>

            <p className="login__demo-tip">
              {isRegister ? (
                <>已有账号？<a href="#" onClick={(e) => { e.preventDefault(); switchMode('login') }}>返回登录</a></>
              ) : USE_REAL_API ? (
                <>还没有账号？<a href="#" onClick={(e) => { e.preventDefault(); switchMode('register') }}>注册账号</a></>
              ) : (
                <>演示模式 · 任意账号密码均可进入 · <a href="#" onClick={(e) => { e.preventDefault(); switchMode('register') }}>注册账号</a></>
              )}
            </p>
          </form>
        </motion.div>
      </motion.div>

      <motion.p
        className="login__copyright"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.1 }}
      >
        LearnPilot · 领域知识个性化资源生成和多智能体系统
      </motion.p>
    </div>
  )
}
