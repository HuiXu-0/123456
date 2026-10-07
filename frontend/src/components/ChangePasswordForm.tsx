import { useState } from 'react'
import { changePassword } from '../services/auth'
import { ApiError } from '../services/api'
import './ChangePasswordForm.css'

/** 客户端最小密码长度，与后端 16.1 / 3.4 同口径（≥6），前置拦住「过弱」。 */
const MIN_LEN = 6

type Status = { kind: 'idle' | 'error' | 'success'; msg?: string }

/**
 * 修改密码表单（问题 3，接入接口 3.4 /auth/change-password）。
 * 三字段（旧 / 新 / 确认）+ 即时校验：新密码 ≥6、两次一致、新旧不同（客户端先行拦截）；
 * 服务端返回 1002 共用码（旧密码错误 / 新密码过弱）时，直接展示后端 message，前端不猜测。
 */
export default function ChangePasswordForm() {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [status, setStatus] = useState<Status>({ kind: 'idle' })

  /* 客户端校验：返回首个错误文案，null 表示通过 */
  const clientError = (): string | null => {
    if (!oldPwd) return '请输入当前密码'
    if (newPwd.length < MIN_LEN) return `新密码长度至少为 ${MIN_LEN} 位`
    if (newPwd === oldPwd) return '新密码不能与当前密码相同'
    if (confirmPwd !== newPwd) return '两次输入的新密码不一致'
    return null
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (submitting) return
    const err = clientError()
    if (err) {
      setStatus({ kind: 'error', msg: err })
      return
    }
    setSubmitting(true)
    setStatus({ kind: 'idle' })
    try {
      await changePassword(oldPwd, newPwd)
      setStatus({ kind: 'success', msg: '密码修改成功，下次登录请使用新密码' })
      setOldPwd('')
      setNewPwd('')
      setConfirmPwd('')
    } catch (e) {
      // 后端共用码（旧密码错误 / 过弱）直接显示服务端文案；其余给通用兜底
      const msg = e instanceof ApiError ? e.message : '修改失败，请稍后再试'
      setStatus({ kind: 'error', msg })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="cpf" onSubmit={submit} noValidate>
      <div className="cpf__field">
        <label className="cpf__label" htmlFor="cpf-old">当前密码</label>
        <input
          id="cpf-old"
          className="cpf__input"
          type="password"
          autoComplete="current-password"
          value={oldPwd}
          onChange={(e) => {
            setOldPwd(e.target.value)
            if (status.kind !== 'idle') setStatus({ kind: 'idle' })
          }}
          placeholder="输入当前登录密码"
        />
      </div>

      <div className="cpf__field">
        <label className="cpf__label" htmlFor="cpf-new">新密码</label>
        <input
          id="cpf-new"
          className="cpf__input"
          type="password"
          autoComplete="new-password"
          value={newPwd}
          onChange={(e) => {
            setNewPwd(e.target.value)
            if (status.kind !== 'idle') setStatus({ kind: 'idle' })
          }}
          placeholder={`至少 ${MIN_LEN} 位`}
        />
      </div>

      <div className="cpf__field">
        <label className="cpf__label" htmlFor="cpf-confirm">确认新密码</label>
        <input
          id="cpf-confirm"
          className="cpf__input"
          type="password"
          autoComplete="new-password"
          value={confirmPwd}
          onChange={(e) => {
            setConfirmPwd(e.target.value)
            if (status.kind !== 'idle') setStatus({ kind: 'idle' })
          }}
          placeholder="再次输入新密码"
        />
      </div>

      {status.kind === 'error' && <p className="cpf__msg cpf__msg--error">{status.msg}</p>}
      {status.kind === 'success' && <p className="cpf__msg cpf__msg--success">{status.msg}</p>}

      <button className="cpf__submit" type="submit" disabled={submitting}>
        {submitting ? '提交中…' : '确认修改'}
      </button>
    </form>
  )
}
