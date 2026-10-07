import { Fragment, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { PageType } from '../App'
import { useJourney } from '../store/journey'
import { useMastery } from '../store/mastery'
import { usePortrait } from '../store/portrait'
import { getUser } from '../services/api'
import { fetchModelRegistry, switchModel, type ModelRegistry } from '../services/models'
import ChangePasswordForm from './ChangePasswordForm'
import './Sidebar.css'

interface SidebarProps {
  currentPage: PageType
  onPageChange: (page: PageType) => void
  collapsed: boolean
  onToggle: () => void
  /** B4-a：admin 登录时渲染「管理」分组，learner 不渲染 */
  isAdmin?: boolean
  /** 退出登录：清票 + 跳回登录页（由 App 提供） */
  onLogout?: () => void
}

/* V3.0: 彩色渐变填充图标 */
const icons = {
  dashboard: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-blue" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#60a5fa" />
          <stop offset="1" stopColor="#2563eb" />
        </linearGradient>
      </defs>
      <rect x="3" y="3" width="7" height="7" rx="2" fill="url(#icon-blue)" opacity="0.9" />
      <rect x="14" y="3" width="7" height="7" rx="2" fill="url(#icon-blue)" opacity="0.7" />
      <rect x="3" y="14" width="7" height="7" rx="2" fill="url(#icon-blue)" opacity="0.7" />
      <rect x="14" y="14" width="7" height="7" rx="2" fill="url(#icon-blue)" opacity="0.5" />
    </svg>
  ),
  profile: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-green" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#34d399" />
          <stop offset="1" stopColor="#059669" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="8" r="4" fill="url(#icon-green)" opacity="0.9" />
      <path d="M4 20c0-4 4-7 8-7s8 3 8 7" fill="url(#icon-green)" opacity="0.6" />
    </svg>
  ),
  knowledgeGraph: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-amber" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#fbbf24" />
          <stop offset="1" stopColor="#d97706" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="5" r="3" fill="url(#icon-amber)" opacity="0.9" />
      <circle cx="5" cy="18" r="2.5" fill="url(#icon-amber)" opacity="0.7" />
      <circle cx="19" cy="18" r="2.5" fill="url(#icon-amber)" opacity="0.7" />
      <line x1="12" y1="8" x2="5" y2="15.5" stroke="url(#icon-amber)" strokeWidth="1.5" opacity="0.5" />
      <line x1="12" y1="8" x2="19" y2="15.5" stroke="url(#icon-amber)" strokeWidth="1.5" opacity="0.5" />
      <line x1="5" y1="18" x2="19" y2="18" stroke="url(#icon-amber)" strokeWidth="1" opacity="0.3" />
    </svg>
  ),
  learningPath: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-purple" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#a78bfa" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <path d="M3 12h4l3-9l3 18l3-9h4" stroke="url(#icon-purple)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="3" cy="12" r="2" fill="url(#icon-purple)" />
      <circle cx="21" cy="12" r="2" fill="url(#icon-purple)" />
    </svg>
  ),
  workflow: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-rose" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#fb7185" />
          <stop offset="1" stopColor="#e11d48" />
        </linearGradient>
      </defs>
      <circle cx="6" cy="6" r="3" fill="url(#icon-rose)" opacity="0.9" />
      <circle cx="18" cy="6" r="3" fill="url(#icon-rose)" opacity="0.7" />
      <circle cx="12" cy="18" r="3" fill="url(#icon-rose)" opacity="0.8" />
      <path d="M6 9v3a3 3 0 003 3h6a3 3 0 003-3V9" stroke="url(#icon-rose)" strokeWidth="1.5" opacity="0.4" />
    </svg>
  ),
  resource: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-teal" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#0d9488" />
        </linearGradient>
      </defs>
      <path d="M4 5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5z" fill="url(#icon-teal)" opacity="0.85" />
      <path d="M13 3v5h5" stroke="#fff" strokeWidth="1.2" opacity="0.7" />
      <line x1="8" y1="12" x2="14" y2="12" stroke="#fff" strokeWidth="1.4" opacity="0.8" strokeLinecap="round" />
      <line x1="8" y1="15.5" x2="13" y2="15.5" stroke="#fff" strokeWidth="1.4" opacity="0.6" strokeLinecap="round" />
    </svg>
  ),
  /* B4-a 管理分组图标：知识库（数据桶）/ Prompt（模板编辑）/ 指标（仪表） */
  adminKb: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-cyan" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#67e8f9" />
          <stop offset="1" stopColor="#0e7490" />
        </linearGradient>
      </defs>
      <ellipse cx="12" cy="5.5" rx="7" ry="2.8" fill="url(#icon-cyan)" opacity="0.9" />
      <path d="M5 5.5v6c0 1.55 3.13 2.8 7 2.8s7-1.25 7-2.8v-6" fill="url(#icon-cyan)" opacity="0.6" />
      <path d="M5 11.5v6c0 1.55 3.13 2.8 7 2.8s7-1.25 7-2.8v-6" fill="url(#icon-cyan)" opacity="0.35" />
    </svg>
  ),
  adminPrompt: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-indigo" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#a5b4fc" />
          <stop offset="1" stopColor="#4338ca" />
        </linearGradient>
      </defs>
      <rect x="3" y="4" width="18" height="14" rx="3" fill="url(#icon-indigo)" opacity="0.55" />
      <path d="M7 9l3 2.5L7 14" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
      <line x1="12.5" y1="14" x2="17" y2="14" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" opacity="0.8" />
      <circle cx="18.5" cy="19.5" r="3" fill="url(#icon-indigo)" opacity="0.95" />
      <path d="M17.3 19.5l.9.9 1.5-1.8" stroke="#fff" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  adminMetrics: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-lime" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#bef264" />
          <stop offset="1" stopColor="#4d7c0f" />
        </linearGradient>
      </defs>
      <path d="M4 17a8 8 0 1 1 16 0" stroke="url(#icon-lime)" strokeWidth="2.4" strokeLinecap="round" opacity="0.85" />
      <line x1="12" y1="17" x2="16" y2="11" stroke="url(#icon-lime)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="12" cy="17" r="2" fill="url(#icon-lime)" />
    </svg>
  ),
  /* B9 用户管理：双人轮廓 */
  adminUsers: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-slate" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#94a3b8" />
          <stop offset="1" stopColor="#475569" />
        </linearGradient>
      </defs>
      <circle cx="9" cy="8" r="3.4" fill="url(#icon-slate)" opacity="0.9" />
      <path d="M3 20c0-3.2 2.7-5.6 6-5.6s6 2.4 6 5.6" fill="url(#icon-slate)" opacity="0.55" />
      <circle cx="17.5" cy="9" r="2.6" fill="url(#icon-slate)" opacity="0.7" />
      <path d="M15.5 14.6c3 .15 5 2.3 5 5.4" stroke="url(#icon-slate)" strokeWidth="1.6" strokeLinecap="round" opacity="0.5" />
    </svg>
  ),
  /* 文档学习：上传的文档 + 高亮生成射线（基于文档生成多模态资料） */
  docLearn: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-doc" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#7dd3fc" />
          <stop offset="1" stopColor="#4f8fbf" />
        </linearGradient>
      </defs>
      <path d="M6 3h7l5 5v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" fill="url(#icon-doc)" opacity="0.85" />
      <path d="M13 3v5h5" stroke="#fff" strokeWidth="1.2" opacity="0.7" />
      <path d="M8.5 12.5l2 2 4-4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.95" />
      <circle cx="18.5" cy="17.5" r="3.2" fill="#fff" opacity="0.9" />
      <path d="M18.5 15.8v1.7l1.1.7" stroke="url(#icon-doc)" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  ),
  /* 模型管理：AI 芯片（多模型接入 + 切换） */
  modelManage: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-model" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#c4b5fd" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <rect x="6" y="6" width="12" height="12" rx="2.4" fill="url(#icon-model)" opacity="0.9" />
      <rect x="9.2" y="9.2" width="5.6" height="5.6" rx="1.2" fill="#fff" opacity="0.85" />
      <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" stroke="url(#icon-model)" strokeWidth="1.6" strokeLinecap="round" opacity="0.75" />
    </svg>
  ),
  /* 我的资源库：分层资产盒（多形态资源归档） */
  myResources: (
    <svg viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="icon-amber" x1="0" y1="0" x2="24" y2="24">
          <stop stopColor="#fcd68a" />
          <stop offset="1" stopColor="#c58940" />
        </linearGradient>
      </defs>
      <rect x="3.5" y="4" width="17" height="5" rx="1.6" fill="url(#icon-amber)" opacity="0.9" />
      <rect x="4.8" y="9.6" width="14.4" height="4.4" rx="1.4" fill="url(#icon-amber)" opacity="0.6" />
      <rect x="4.8" y="14.6" width="14.4" height="4.4" rx="1.4" fill="url(#icon-amber)" opacity="0.4" />
      <line x1="10" y1="6.5" x2="14" y2="6.5" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" opacity="0.85" />
    </svg>
  ),
}

interface MenuItem {
  id: PageType
  icon: JSX.Element
  label: string
  description: string
  seq?: number
  /** 进入该步骤的前置条件（用于软门控提示） */
  requires?: 'hasDiagnosed' | 'hasGeneratedPath'
  beta?: boolean
}

/** 核心学习（主线，有先后依赖，标序号①②③）*/
const mainlineItems: MenuItem[] = [
  { id: 'profile', icon: icons.profile, label: '画像诊断', description: '知识能力评估', seq: 1 },
  { id: 'learning-path', icon: icons.learningPath, label: '学习路径', description: '个性化导航', seq: 2, requires: 'hasDiagnosed' },
  { id: 'learning-resource', icon: icons.resource, label: '学习资源', description: 'AI讲义·测试题', seq: 3, requires: 'hasGeneratedPath' },
]

/** 学情管家（统领位：中枢首页，置顶第一组）*/
const stewardItems: MenuItem[] = [
  { id: 'dashboard', icon: icons.dashboard, label: '学情管家', description: '中枢首页 · 统筹全局' },
]

/** 文档学习（与内置课程平行的学习方式，不再埋在工具里）*/
const docItems: MenuItem[] = [
  { id: 'document-learning', icon: icons.docLearn, label: '文档学习', description: '传文档 · 生成多模态资料' },
]

/** 我的空间（个人资产）*/
const spaceItems: MenuItem[] = [
  { id: 'my-resources', icon: icons.myResources, label: '我的资源库', description: '生成资产 · 查看下载' },
]

/** 工具（辅助能力）*/
const toolItems: MenuItem[] = [
  { id: 'workflow', icon: icons.workflow, label: 'Agent工作流', description: 'AI协同可视化' },
  { id: 'knowledge-graph', icon: icons.knowledgeGraph, label: '知识图谱', description: '可视化拓扑', beta: true },
  { id: 'model-management', icon: icons.modelManage, label: '模型管理', description: '多模型接入 · 切换' },
]

/** learner 侧栏分组顺序（信息架构：学情管家统领置顶 → 核心学习 → 文档学习 → 我的空间 → 工具）*/
const learnerGroups: { title: string; items: MenuItem[] }[] = [
  { title: '学情管家', items: stewardItems },
  { title: '核心学习', items: mainlineItems },
  { title: '文档学习', items: docItems },
  { title: '我的空间', items: spaceItems },
  { title: '工具', items: toolItems },
]
const learnerItemCount = learnerGroups.reduce((n, g) => n + g.items.length, 0)

/** 管理（仅 admin 渲染，B4 阶段）*/
const adminItems: MenuItem[] = [
  { id: 'admin-kb', icon: icons.adminKb, label: '知识库管理', description: '文档入库 · 检索测试' },
  { id: 'admin-prompts', icon: icons.adminPrompt, label: 'Prompt管理', description: 'Agent模板热更新' },
  { id: 'admin-metrics', icon: icons.adminMetrics, label: '指标看板', description: '幻觉率 · 适配 · 覆盖' },
  { id: 'admin-users', icon: icons.adminUsers, label: '用户管理', description: '账号 · 进度 · 启停' },
]

export default function Sidebar({ currentPage, onPageChange, collapsed, onToggle, isAdmin = false, onLogout }: SidebarProps) {
  const hasDiagnosed = useJourney((s) => s.hasDiagnosed)
  const hasGeneratedPath = useJourney((s) => s.hasGeneratedPath)
  const resetJourney = useJourney((s) => s.resetJourney)
  const resetMastery = useMastery((s) => s.reset)
  const resetPortrait = usePortrait((s) => s.reset)

  /* 左下角用户菜单：展开「设置 / 退出登录」；点外部或 Esc 关闭 */
  const [menuOpen, setMenuOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  /* 设置面板内「修改密码」表单展开态 */
  const [pwOpen, setPwOpen] = useState(false)
  const userWrapRef = useRef<HTMLDivElement>(null)

  /* 设置面板「生成模型」切换（接口文档 21）：打开面板时拉取注册表，切换即生效 */
  const [modelReg, setModelReg] = useState<ModelRegistry | null>(null)
  const [modelBusy, setModelBusy] = useState(false)
  const [modelErr, setModelErr] = useState('')
  useEffect(() => {
    if (!settingsOpen) return
    let cancelled = false
    fetchModelRegistry()
      .then((reg) => {
        if (!cancelled) {
          setModelReg(reg)
          setModelErr('')
        }
      })
      .catch(() => {
        if (!cancelled) setModelErr('模型列表加载失败，请稍后重试')
      })
    return () => {
      cancelled = true
    }
  }, [settingsOpen])

  const onSwitchModel = async (modelId: string) => {
    if (!modelReg || modelBusy || modelId === modelReg.current) return
    setModelBusy(true)
    try {
      setModelReg(await switchModel(modelId))
      setModelErr('')
    } catch {
      setModelErr('切换失败，仍使用原模型')
    } finally {
      setModelBusy(false)
    }
  }
  const currentModel = modelReg?.models.find((m) => m.isCurrent) ?? null

  useEffect(() => {
    if (!menuOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (userWrapRef.current && !userWrapRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMenuOpen(false)
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const isLocked = (item: MenuItem) => {
    if (item.requires === 'hasDiagnosed') return !hasDiagnosed
    if (item.requires === 'hasGeneratedPath') return !hasGeneratedPath
    return false
  }

  const renderItem = (item: MenuItem, index: number) => {
    const active = currentPage === item.id
    const locked = isLocked(item)
    return (
      <motion.button
        key={item.id}
        className={`sidebar__nav-item ${active ? 'sidebar__nav-item--active' : ''} ${locked ? 'sidebar__nav-item--locked' : ''}`}
        onClick={() => onPageChange(item.id)}
        title={locked ? '建议先完成前置步骤' : undefined}
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: index * 0.06, duration: 0.4 }}
        whileHover={{ x: collapsed ? 0 : 4, transition: { duration: 0.2 } }}
        whileTap={{ scale: 0.98 }}
      >
        <span className="sidebar__nav-icon">{item.icon}</span>
        {!collapsed && (
          <span className="sidebar__nav-content">
            <span className="sidebar__nav-label">
              {item.seq && <span className="sidebar__nav-seq">{item.seq}</span>}
              {item.label}
              {item.beta && <span className="sidebar__beta-tag">Beta</span>}
              {locked && (
                <svg className="sidebar__nav-lock" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="11" width="14" height="9" rx="2" />
                  <path d="M8 11V8a4 4 0 0 1 8 0v3" />
                </svg>
              )}
            </span>
            <span className="sidebar__nav-desc">{item.description}</span>
          </span>
        )}
        {active && (
          <motion.div
            className="sidebar__nav-indicator"
            layoutId="sidebar-active-indicator"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          />
        )}
        {active && <div className="sidebar__nav-glow" />}
      </motion.button>
    )
  }

  return (
    <>
    <motion.aside
      className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}
      initial={{ x: -20, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
    >
      {/* Logo 区域 */}
      <div className="sidebar__header">
        <motion.div
          className="sidebar__logo"
          whileHover={{ scale: 1.02 }}
        >
          <img className="sidebar__logo-mark" src="/logo/logo-mark.png" alt="智学中枢" />
          <div className="sidebar__logo-text">
            <span className="sidebar__logo-title">智学中枢</span>
            <span className="sidebar__logo-subtitle">AI Learning Platform</span>
          </div>
        </motion.div>

        <button className="sidebar__toggle" onClick={onToggle} aria-label="折叠侧边栏">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            {collapsed ? (
              <path d="M9 18l6-6-6-6" />
            ) : (
              <path d="M15 18l-6-6 6-6" />
            )}
          </svg>
        </button>
      </div>

      {/* 导航区域：按信息架构分组（核心学习 / 学情管家 / 文档学习 / 我的空间 / 工具） */}
      <nav className="sidebar__nav">
        {learnerGroups.map((group, gi) => {
          const offset = learnerGroups.slice(0, gi).reduce((n, g) => n + g.items.length, 0)
          const steward = group.title === '学情管家'
          return (
            <Fragment key={group.title}>
              {gi > 0 && <div className="sidebar__nav-divider" />}
              {!collapsed && (
                <p className={`sidebar__nav-group-title${steward ? ' sidebar__nav-group-title--steward' : ''}`}>
                  {group.title}
                </p>
              )}
              {group.items.map((item, i) => renderItem(item, offset + i))}
            </Fragment>
          )
        })}

        {isAdmin && (
          <>
            <div className="sidebar__nav-divider" />
            {!collapsed && <p className="sidebar__nav-group-title">管理</p>}
            {adminItems.map((item, i) => renderItem(item, learnerItemCount + i))}
          </>
        )}
      </nav>

      {/* 用户资料区 - 毛玻璃卡片 + 下拉菜单 */}
      {!collapsed && (
        <div className="sidebar__user-wrap" ref={userWrapRef}>
          <motion.button
            type="button"
            className={`sidebar__user ${menuOpen ? 'sidebar__user--open' : ''}`}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5, duration: 0.4 }}
            whileHover={{ y: -2 }}
            onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <div className="sidebar__user-avatar">
              <span>{isAdmin ? '管' : '学'}</span>
              <div className="sidebar__user-avatar-glow" />
            </div>
            <div className="sidebar__user-info">
              <span className="sidebar__user-name">{getUser()?.displayName ?? '学习者_001'}</span>
              <span className="sidebar__user-level">{isAdmin ? '管理员 · 系统运营' : '中级 · AI领域'}</span>
            </div>
            <motion.svg
              className="sidebar__user-arrow"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              animate={{ rotate: menuOpen ? -90 : 0 }}
            >
              <path d="M9 18l6-6-6-6" />
            </motion.svg>
          </motion.button>

          <AnimatePresence>
            {menuOpen && (
              <motion.div
                className="sidebar__user-menu"
                role="menu"
                initial={{ opacity: 0, y: 8, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.96 }}
                transition={{ duration: 0.16 }}
              >
                <button
                  className="sidebar__user-menu-item"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false)
                    setSettingsOpen(true)
                  }}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                  </svg>
                  设置
                </button>
                <div className="sidebar__user-menu-divider" />
                <button
                  className="sidebar__user-menu-item sidebar__user-menu-item--danger"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false)
                    onLogout?.()
                  }}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                    <polyline points="16 17 21 12 16 7" />
                    <line x1="21" y1="12" x2="9" y2="12" />
                  </svg>
                  退出登录
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* 演示用：重置闭环进度，回到新用户首屏引导 */}
      {!collapsed && (
        <button className="sidebar__reset" onClick={() => { resetJourney(); resetMastery(); resetPortrait() }} title="清除进度，回到新用户引导">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3" />
            <path d="M3 3v5h5" />
          </svg>
          重置引导进度
        </button>
      )}
    </motion.aside>

    {/* 设置面板（骨架占位：改密码下会话接入） */}
    <AnimatePresence>
      {settingsOpen && (
        <motion.div
          className="settings-mask"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => { setSettingsOpen(false); setPwOpen(false) }}
        >
          <motion.div
            className="settings-panel"
            initial={{ opacity: 0, y: 20, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 260, damping: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="settings-panel__close" aria-label="关闭" onClick={() => { setSettingsOpen(false); setPwOpen(false) }}>
              ×
            </button>
            <h3 className="settings-panel__title">设置</h3>
            <p className="settings-panel__sub">账户与偏好设置（持续完善中）</p>

            <div className="settings-panel__section">
              <div className="settings-panel__row">
                <div className="settings-panel__row-text">
                  <span className="settings-panel__row-label">账户</span>
                  <span className="settings-panel__row-value">{getUser()?.displayName ?? '学习者_001'}</span>
                </div>
              </div>
              <div className="settings-panel__row">
                <div className="settings-panel__row-text">
                  <span className="settings-panel__row-label">修改密码</span>
                  <span className="settings-panel__row-value">更换登录密码</span>
                </div>
                <button
                  className="settings-panel__row-btn settings-panel__row-btn--active"
                  onClick={() => setPwOpen((o) => !o)}
                  aria-expanded={pwOpen}
                >
                  {pwOpen ? '收起' : '修改'}
                </button>
              </div>

              <AnimatePresence initial={false}>
                {pwOpen && (
                  <motion.div
                    className="settings-panel__pw"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.22 }}
                  >
                    <ChangePasswordForm />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* 生成模型切换（接口文档 21）：讲义/练习等所有生成走所选模型，失败自动回落 */}
            <div className="settings-panel__section">
              <div className="settings-panel__row">
                <div className="settings-panel__row-text">
                  <span className="settings-panel__row-label">生成模型</span>
                  <span className="settings-panel__row-value">
                    {modelErr || (currentModel ? `当前：${currentModel.label}` : '加载中…')}
                  </span>
                </div>
                {modelReg && (
                  <select
                    className="settings-panel__model-select"
                    value={modelReg.current}
                    disabled={modelBusy}
                    onChange={(e) => onSwitchModel(e.target.value)}
                    aria-label="选择生成模型"
                  >
                    {modelReg.models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                        {m.available ? '' : '（未配 Key·自动回落）'}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <p className="settings-panel__model-hint">
                切换后，讲义 / 练习 / 图解等所有生成将走该模型；模型不可用时自动回落默认模型，不影响使用。
              </p>
            </div>

            <p className="settings-panel__note">更多设置项（通知 / 主题 / 隐私）将于后续版本陆续开放。</p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
    </>
  )
}
