import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import Sidebar from './components/Sidebar'
import ScrollToTop from './components/ScrollToTop'
import PageErrorBoundary from './components/PageErrorBoundary'
import { getUser, clearAuth, getToken, isTokenExpired, USE_REAL_API } from './services/api'
import { useJourney } from './store/journey'
import { useMastery } from './store/mastery'
import './styles/App.css'

// 页面级代码分割：保留现有状态路由结构，只把页面实现移出首屏入口包。
const GlobalTutorLayer = lazy(() => import('./components/GlobalTutorLayer'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ProfileBuilder = lazy(() => import('./pages/ProfileBuilder'))
const LearningPath = lazy(() => import('./pages/LearningPath'))
const AgentWorkflow = lazy(() => import('./pages/AgentWorkflow'))
const LearningResource = lazy(() => import('./pages/LearningResource'))
const DocumentLearning = lazy(() => import('./pages/DocumentLearning'))
const MyResourceLibrary = lazy(() => import('./pages/MyResourceLibrary'))
const KnowledgeGraph = lazy(() => import('./pages/KnowledgeGraph'))
const ModelManagement = lazy(() => import('./pages/ModelManagement'))
const AdminKB = lazy(() => import('./pages/admin/AdminKB'))
const AdminPrompts = lazy(() => import('./pages/admin/AdminPrompts'))
const AdminMetrics = lazy(() => import('./pages/admin/AdminMetrics'))
const AdminUsers = lazy(() => import('./pages/admin/AdminUsers'))
const Login = lazy(() => import('./pages/Login'))
const WelcomePage = lazy(() => import('./pages/WelcomePage'))

const PageLoading = () => (
  <div className="app__page-loading" role="status" aria-live="polite">
    页面加载中…
  </div>
)

export type PageType =
  | 'dashboard'
  | 'profile'
  | 'learning-path'
  | 'learning-resource'
  | 'document-learning'
  | 'my-resources'
  | 'workflow'
  | 'knowledge-graph'
  | 'model-management'
  | 'admin-kb'
  | 'admin-prompts'
  | 'admin-metrics'
  | 'admin-users'
type Stage = 'landing' | 'login' | 'app'

/** B4-a 管理端：hash 深链 ↔ 页面映射（learner 直接敲 URL 时由守卫拦回首页） */
const ADMIN_HASH_TO_PAGE: Record<string, PageType> = {
  '#/admin/kb': 'admin-kb',
  '#/admin/prompts': 'admin-prompts',
  '#/admin/metrics': 'admin-metrics',
  '#/admin/users': 'admin-users',
}
const ADMIN_PAGE_TO_HASH: Partial<Record<PageType, string>> = {
  'admin-kb': '#/admin/kb',
  'admin-prompts': '#/admin/prompts',
  'admin-metrics': '#/admin/metrics',
  'admin-users': '#/admin/users',
}
const isAdminPage = (page: PageType) => page.startsWith('admin-')

/**
 * 刷新后恢复登录态（issue#3）：联调下 token 持久化在 localStorage，启动时若 token
 * 存在且未过期则直接进应用，跳过 Landing/Login（与 journey/portrait 持久化 store 同
 * 风格）；过期 / 无 token 则照常落地登录页。同步计算初始 stage 避免「先闪登录再跳转」。
 */
const restorableSession = USE_REAL_API && !!getToken() && !isTokenExpired()
const restoredUser = restorableSession ? getUser() : null
const restoredRole: 'learner' | 'admin' = restoredUser?.role === 'admin' ? 'admin' : 'learner'

function App() {
  const [stage, setStage] = useState<Stage>(restorableSession ? 'app' : 'landing')
  const [currentPage, setCurrentPage] = useState<PageType>(
    restorableSession && restoredRole === 'admin' ? 'admin-kb' : 'dashboard'
  )
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  /** 登录响应 role（15.1）：admin 进管理端视图；mock 模式无登录态时按 learner 处理 */
  const [role, setRole] = useState<'learner' | 'admin'>(restoredRole)
  const isAdmin = role === 'admin'
  /** 主滚动容器引用：页面切换时由 ScrollToTop 归零 */
  const mainRef = useRef<HTMLElement>(null)

  /** 刷新恢复登录态时，拉取旅程 / 掌握度权威态（与登录回调同口径，仅挂载执行一次）。 */
  useEffect(() => {
    if (!restorableSession) return
    Promise.all([useJourney.getState().loadJourney(), useMastery.getState().load()]).catch((e) =>
      console.error('[app] 恢复会话拉取状态失败', e)
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 统一导航入口：非 admin 访问管理页一律拦回首页；管理页同步 hash 深链 */
  const navigate = (page: PageType) => {
    const target = isAdminPage(page) && !isAdmin ? 'dashboard' : page
    setCurrentPage(target)
    const hash = ADMIN_PAGE_TO_HASH[target]
    if (hash) {
      if (window.location.hash !== hash) window.location.hash = hash
    } else if (ADMIN_HASH_TO_PAGE[window.location.hash]) {
      // 离开管理页时清掉管理 hash，不触碰 learner 侧既有 URL 行为
      history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }

  /** 管理路由守卫：进入应用后监听 #/admin/* 直敲与变更，learner 拦截回首页 */
  useEffect(() => {
    if (stage !== 'app') return
    const applyHash = () => {
      const target = ADMIN_HASH_TO_PAGE[window.location.hash]
      if (!target) return
      if (isAdmin) {
        setCurrentPage(target)
      } else {
        history.replaceState(null, '', window.location.pathname + window.location.search)
        setCurrentPage('dashboard')
      }
    }
    applyHash()
    window.addEventListener('hashchange', applyHash)
    return () => window.removeEventListener('hashchange', applyHash)
  }, [stage, isAdmin])

  /** 登录/注册成功回调。注册新学习者时 toProfile=true → 直接进入画像诊断引导（B9）。 */
  const handleLogin = (opts?: { toProfile?: boolean }) => {
    const user = getUser()
    const nextRole = user?.role === 'admin' ? 'admin' : 'learner'
    setRole(nextRole)
    if (nextRole === 'admin') setCurrentPage('admin-kb')
    else setCurrentPage(opts?.toProfile ? 'profile' : 'dashboard')
    setStage('app')
  }

  /** 退出登录：清 JWT/用户态 → 复位视图 → 跳回登录页。 */
  const handleLogout = () => {
    clearAuth()
    setRole('learner')
    setCurrentPage('dashboard')
    setStage('login')
  }

  const renderPage = () => {
    // 双保险：即使状态被直接置为管理页，learner 也渲染首页
    if (isAdminPage(currentPage) && !isAdmin) return <Dashboard onNavigate={navigate} />
    switch (currentPage) {
      case 'dashboard':
        return <Dashboard onNavigate={navigate} />
      case 'profile':
        return <ProfileBuilder onNavigate={navigate} />
      case 'learning-path':
        return <LearningPath onNavigate={navigate} />
      case 'learning-resource':
        return <LearningResource onNavigate={navigate} />
      case 'document-learning':
        return <DocumentLearning onNavigate={navigate} />
      case 'my-resources':
        return <MyResourceLibrary onNavigate={navigate} />
      case 'workflow':
        return <AgentWorkflow onNavigate={navigate} />
      case 'knowledge-graph':
        return <KnowledgeGraph onNavigate={navigate} />
      case 'model-management':
        return <ModelManagement onNavigate={navigate} />
      case 'admin-kb':
        return <AdminKB />
      case 'admin-prompts':
        return <AdminPrompts />
      case 'admin-metrics':
        return <AdminMetrics />
      case 'admin-users':
        return <AdminUsers />
      default:
        return <Dashboard onNavigate={navigate} />
    }
  }

  if (stage === 'landing') {
    return (
      <Suspense fallback={<PageLoading />}>
        <WelcomePage onEnter={() => setStage('login')} />
      </Suspense>
    )
  }

  if (stage === 'login') {
    return (
      <AnimatePresence mode="wait">
        <Suspense fallback={<PageLoading />}>
          <Login key="login" onLogin={handleLogin} />
        </Suspense>
      </AnimatePresence>
    )
  }

  return (
    <div className="app">
      {/* Background Pattern */}
      <div className="app__background">
        <div className="app__gradient-orb app__gradient-orb--1" />
        <div className="app__gradient-orb app__gradient-orb--2" />
        <div className="app__gradient-orb app__gradient-orb--3" />
      </div>

      {/* Sidebar */}
      <Sidebar
        currentPage={currentPage}
        onPageChange={navigate}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        isAdmin={isAdmin}
        onLogout={handleLogout}
      />

      {/* 页面切换时主滚动容器归零，避免停在上一页中部 */}
      <ScrollToTop dep={currentPage} containerRef={mainRef} />

      {/* Main Content
          导航靠 currentPage 状态机重挂载页面。此处刻意不用 AnimatePresence「mode=wait」
          包裹：mode=wait 会把新页挂载阻塞到旧页 exit 动画完成，快速来回切换时退出回调
          可能丢失 → 新页一直不挂载、内容区白屏，只能整页刷新（issue#2/3 现象）。
          改为按 key 重挂载的 motion.div：换页即立刻挂载并播放进入动画，无 exit 门控、
          不会卡死；进入动画保留。外层 PageErrorBoundary 兜底单页渲染异常。 */}
      <main ref={mainRef} className={`app__main ${sidebarCollapsed ? 'app__main--expanded' : ''}`}>
        <motion.div
          key={currentPage}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, ease: 'easeInOut' }}
          className="app__content"
        >
          <PageErrorBoundary resetKey={currentPage}>
            <Suspense fallback={<PageLoading />}>{renderPage()}</Suspense>
          </PageErrorBoundary>
        </motion.div>
      </main>

      {/* 全局即时辅导层（B-2 全局化）：正文内容页选中即问 + 常驻辅导 dock，一次接入全站可用。
          仅学习内容页挂载，避免管理端 / 落地页出现无关入口。 */}
      {!isAdmin && (currentPage === 'learning-resource' || currentPage === 'document-learning') && (
        <Suspense fallback={null}>
          <GlobalTutorLayer />
        </Suspense>
      )}
    </div>
  )
}

export default App
