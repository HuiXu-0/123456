import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import PageHeader from '../../components/PageHeader'
import { USE_REAL_API } from '../../services/api'
import {
  listAdminUsers,
  resetUserProgress,
  toggleUserActive,
  type AdminUserRow,
} from '../../services/admin'
import './admin.css'

/* 与其余管理页一致的入场 stagger */
const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
}
const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0 },
}

function formatTime(iso: string | null): string {
  if (!iso) return '从未登录'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUserRow[]>([])
  const [total, setTotal] = useState(0)
  const [listError, setListError] = useState<string | null>(null)

  /* 重置确认弹层 + 操作中行 + 反馈 */
  const [confirmReset, setConfirmReset] = useState<AdminUserRow | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const page = await listAdminUsers({ pageSize: 100 })
      setUsers(page.items)
      setTotal(page.total)
      setListError(null)
    } catch (err) {
      setListError(err instanceof Error ? err.message : '获取用户列表失败')
    }
  }, [])

  useEffect(() => {
    if (USE_REAL_API) void refresh()
  }, [refresh])

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 3200)
  }

  const handleReset = async () => {
    if (!confirmReset || busyId) return
    setBusyId(confirmReset.userId)
    try {
      const res = await resetUserProgress(confirmReset.userId)
      showToast(`已重置「${confirmReset.username}」的学习进度 · 清除 ${res.masteryCleared} 个掌握点`)
      setConfirmReset(null)
      await refresh()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '重置失败')
      setConfirmReset(null)
    } finally {
      setBusyId(null)
    }
  }

  const handleToggle = async (row: AdminUserRow) => {
    if (busyId) return
    setBusyId(row.userId)
    try {
      const res = await toggleUserActive(row.userId)
      showToast(`已${res.isActive ? '启用' : '禁用'}「${row.username}」`)
      await refresh()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusyId(null)
    }
  }

  const activeCount = users.filter((u) => u.isActive).length

  return (
    <motion.div className="ausr" variants={containerVariants} initial="hidden" animate="visible">
      <PageHeader
        title="用户管理"
        highlight="用户"
        subtitle="账号总览 · 学习进度重置 · 账号启停"
        badges={[
          { label: '用户', value: total },
          { label: '启用', value: activeCount, tone: 'accent' },
        ]}
      />

      {!USE_REAL_API && (
        <motion.div className="akb__offline glass-card" variants={itemVariants}>
          当前为前端 Mock 演示模式（未设 <code>VITE_USE_REAL_API=true</code>），用户管理需连接后端使用。
        </motion.div>
      )}

      {listError && (
        <motion.p className="akb__error" variants={itemVariants}>{listError}</motion.p>
      )}
      {!listError && USE_REAL_API && users.length === 0 && (
        <motion.p className="akb__empty" variants={itemVariants}>暂无用户</motion.p>
      )}

      {users.length > 0 && (
        <motion.section className="ausr__table glass-card" variants={itemVariants}>
          <div className="ausr__row ausr__row--head">
            <span>用户</span>
            <span>角色</span>
            <span>诊断</span>
            <span>已通过</span>
            <span>最近活跃</span>
            <span>状态</span>
            <span className="ausr__col-actions">操作</span>
          </div>
          <AnimatePresence initial={false}>
            {users.map((u) => {
              const isAdmin = u.role === 'admin'
              const busy = busyId === u.userId
              return (
                <motion.div
                  key={u.userId}
                  className="ausr__row"
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <span className="ausr__user">
                    <span className="ausr__avatar">{isAdmin ? '管' : '学'}</span>
                    <span className="ausr__uname">{u.username}</span>
                  </span>
                  <span>
                    <span className={`ausr__badge ${isAdmin ? 'ausr__badge--admin' : 'ausr__badge--learner'}`}>
                      {isAdmin ? '管理员' : '学习者'}
                    </span>
                  </span>
                  <span className="ausr__muted">{u.hasDiagnosed ? '已诊断' : '未诊断'}</span>
                  <span className="ausr__count">{u.passedKpCount}</span>
                  <span className="ausr__muted ausr__time">{formatTime(u.lastActiveAt)}</span>
                  <span>
                    <span className={`agent-status ${u.isActive ? 'agent-status--success' : 'agent-status--error'}`}>
                      <span className={`status-dot ${u.isActive ? 'status-dot--success' : 'status-dot--idle'}`} />
                      {u.isActive ? '启用' : '已禁用'}
                    </span>
                  </span>
                  <span className="ausr__actions">
                    {isAdmin ? (
                      <span className="ausr__no-op" title="管理员账号无学习进度且不可禁用">—</span>
                    ) : (
                      <>
                        <button
                          className="ausr__btn"
                          disabled={busy}
                          onClick={() => setConfirmReset(u)}
                        >
                          重置进度
                        </button>
                        <button
                          className={`ausr__btn ${u.isActive ? 'ausr__btn--danger' : 'ausr__btn--ok'}`}
                          disabled={busy}
                          onClick={() => void handleToggle(u)}
                        >
                          {busy ? '处理中…' : u.isActive ? '禁用' : '启用'}
                        </button>
                      </>
                    )}
                  </span>
                </motion.div>
              )
            })}
          </AnimatePresence>
        </motion.section>
      )}

      {/* ===== 重置确认弹层（复用知识库删除弹层样式）===== */}
      <AnimatePresence>
        {confirmReset && (
          <motion.div
            className="akb__modal-mask"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => !busyId && setConfirmReset(null)}
          >
            <motion.div
              className="akb__modal glass-card"
              initial={{ opacity: 0, scale: 0.94, y: 12 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
              onClick={(e) => e.stopPropagation()}
            >
              <h4>重置学习进度？</h4>
              <p>
                将清空「{confirmReset.username}」的全部掌握度（{confirmReset.passedKpCount} 个已通过知识点）
                与学习旅程，账号回到<strong>未诊断初始态</strong>，操作不可恢复。
              </p>
              <div className="akb__modal-actions">
                <button className="btn btn--ghost" onClick={() => setConfirmReset(null)} disabled={!!busyId}>
                  取消
                </button>
                <button className="btn btn--primary akb__modal-danger" onClick={() => void handleReset()} disabled={!!busyId}>
                  {busyId ? '重置中…' : '确认重置'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ===== 操作反馈 toast ===== */}
      <AnimatePresence>
        {toast && (
          <motion.div
            className="akb__toast glass-card"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
