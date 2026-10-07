/**
 * 模型管理独立页（接口文档 21，CC-model-management-page.md）
 *
 * - 列出内置 + 用户自建模型（显示名/provider/model_id/状态：当前使用·可用·未配Key）；
 * - 界面添加/编辑/删除自建模型（显示名 + provider + base_url + model_id + api_key）；
 * - 一键切换当前模型（自建配置仅本人生效）；测试连通性（保存前后均可测）。
 *
 * key 安全（§3 红线）：apiKey 仅存在于表单瞬时 state，提交/关闭表单即清空——
 * 不进 localStorage、不进持久化 store；展示一律用后端脱敏形（****后四位）。
 */
import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import PageHeader from '../components/PageHeader'
import type { PageType } from '../App'
import { USE_REAL_API } from '../services/api'
import {
  addModelConfig,
  deleteModelConfig,
  fetchModelRegistry,
  switchModel,
  testModelConfig,
  updateModelConfig,
  type ModelConfigInput,
  type ModelInfo,
  type ModelRegistry,
  type ModelTestResult,
} from '../services/models'
import './ModelManagement.css'

interface Props {
  onNavigate: (page: PageType) => void
}

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06 } },
}
const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
}

const PROVIDER_LABEL: Record<string, string> = {
  deepseek: 'DeepSeek',
  modelscope: '魔搭 ModelScope',
  openai: 'OpenAI 兼容',
  mock: '离线 Mock',
}

/** provider 选择时的 base_url 预填（可改） */
const PROVIDER_BASE_URL: Record<string, string> = {
  modelscope: 'https://api-inference.modelscope.cn/v1',
  deepseek: 'https://api.deepseek.com',
  openai: '',
}

interface FormState {
  id: string | null // null = 新增；umc_xxx = 编辑
  label: string
  provider: 'modelscope' | 'deepseek' | 'openai'
  baseUrl: string
  modelId: string
  apiKey: string // 瞬时态：提交/关闭即清空，不留存
  keyMasked?: string // 编辑时展示原 key 脱敏形
}

const EMPTY_FORM: FormState = {
  id: null,
  label: '',
  provider: 'modelscope',
  baseUrl: PROVIDER_BASE_URL.modelscope,
  modelId: '',
  apiKey: '',
}

export default function ModelManagement({ onNavigate }: Props) {
  const [reg, setReg] = useState<ModelRegistry | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('') // 正在切换/删除/测试的卡片 id
  const [confirmDelId, setConfirmDelId] = useState('') // 两步删除确认
  const [toast, setToast] = useState<string | null>(null)

  /* 添加/编辑弹窗 */
  const [form, setForm] = useState<FormState | null>(null)
  const [formBusy, setFormBusy] = useState(false)
  const [formErr, setFormErr] = useState('')
  const [formTest, setFormTest] = useState<ModelTestResult | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 3600)
  }

  const load = useCallback(async () => {
    try {
      setReg(await fetchModelRegistry())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '模型列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  /* ---------- 卡片操作 ---------- */

  const handleSwitch = async (m: ModelInfo) => {
    if (busyId || m.isCurrent) return
    setBusyId(m.id)
    try {
      setReg(await switchModel(m.id))
      showToast(`已切换当前模型：${m.label} · 后续生成即走该模型`)
    } catch (e) {
      showToast(e instanceof Error ? e.message : '切换失败，仍使用原模型')
    } finally {
      setBusyId('')
    }
  }

  const handleCardTest = async (m: ModelInfo) => {
    if (busyId) return
    setBusyId(m.id)
    try {
      const r = await testModelConfig({ configId: m.id })
      showToast(r.ok ? `✅ ${m.label}：${r.message}` : `❌ ${m.label}：${r.message}`)
    } catch (e) {
      showToast(`❌ ${m.label}：${e instanceof Error ? e.message : '测试失败'}`)
    } finally {
      setBusyId('')
    }
  }

  const handleDelete = async (m: ModelInfo) => {
    if (busyId) return
    if (confirmDelId !== m.id) {
      setConfirmDelId(m.id)
      window.setTimeout(() => setConfirmDelId((prev) => (prev === m.id ? '' : prev)), 4000)
      return
    }
    setConfirmDelId('')
    setBusyId(m.id)
    try {
      await deleteModelConfig(m.id)
      await load()
      showToast(`已删除模型配置：${m.label}${m.isCurrent ? '（已回落默认模型）' : ''}`)
    } catch (e) {
      showToast(e instanceof Error ? e.message : '删除失败')
    } finally {
      setBusyId('')
    }
  }

  /* ---------- 添加/编辑弹窗 ---------- */

  const openAdd = () => {
    setForm({ ...EMPTY_FORM })
    setFormErr('')
    setFormTest(null)
  }

  const openEdit = (m: ModelInfo) => {
    setForm({
      id: m.id,
      label: m.label,
      provider: (['modelscope', 'deepseek', 'openai'].includes(m.provider) ? m.provider : 'openai') as FormState['provider'],
      baseUrl: m.baseUrl ?? '',
      modelId: m.modelId ?? '',
      apiKey: '', // 永不回填明文
      keyMasked: m.apiKeyMasked,
    })
    setFormErr('')
    setFormTest(null)
  }

  /** 关闭弹窗：瞬时 apiKey 一并丢弃（key 不留存红线） */
  const closeForm = () => {
    setForm(null)
    setFormErr('')
    setFormTest(null)
  }

  const onProviderChange = (p: FormState['provider']) => {
    setForm((f) => {
      if (!f) return f
      const keepUrl = f.baseUrl && f.baseUrl !== PROVIDER_BASE_URL[f.provider]
      return { ...f, provider: p, baseUrl: keepUrl ? f.baseUrl : PROVIDER_BASE_URL[p] }
    })
  }

  const formValid =
    !!form &&
    form.label.trim() !== '' &&
    /^https?:\/\//i.test(form.baseUrl.trim()) &&
    form.modelId.trim() !== '' &&
    (form.id !== null || form.apiKey.trim() !== '') // 新增必填 key，编辑可留空

  /** 可测试：url/model 已填，且（填了 key）或（编辑态可用库中 key） */
  const formTestable =
    !!form &&
    /^https?:\/\//i.test(form.baseUrl.trim()) &&
    form.modelId.trim() !== '' &&
    (form.apiKey.trim() !== '' || form.id !== null)

  const handleFormTest = async () => {
    if (!form || formBusy) return
    setFormBusy(true)
    setFormTest(null)
    try {
      const r =
        form.apiKey.trim() === '' && form.id
          ? await testModelConfig({
              configId: form.id, // 编辑未改 key → 用库中解密 key 测，带上表单里可能已改的 url/model
              baseUrl: form.baseUrl.trim(),
              modelId: form.modelId.trim(),
            })
          : await testModelConfig({
              baseUrl: form.baseUrl.trim(),
              modelId: form.modelId.trim(),
              apiKey: form.apiKey.trim(),
            })
      setFormTest(r)
    } catch (e) {
      setFormTest({ ok: false, latencyMs: 0, message: e instanceof Error ? e.message : '测试失败' })
    } finally {
      setFormBusy(false)
    }
  }

  const handleFormSave = async () => {
    if (!form || !formValid || formBusy) return
    setFormBusy(true)
    setFormErr('')
    try {
      const payload: ModelConfigInput = {
        label: form.label.trim(),
        provider: form.provider,
        baseUrl: form.baseUrl.trim(),
        modelId: form.modelId.trim(),
        ...(form.apiKey.trim() ? { apiKey: form.apiKey.trim() } : {}),
      }
      if (form.id === null) {
        await addModelConfig(payload as Required<ModelConfigInput>)
        showToast(`已添加模型：${payload.label} · 可点「设为当前」启用`)
      } else {
        await updateModelConfig(form.id, payload)
        showToast(`已更新模型配置：${payload.label}`)
      }
      closeForm() // 关闭即清空瞬时 apiKey
      await load()
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : '保存失败')
    } finally {
      setFormBusy(false)
    }
  }

  /* ---------- 渲染 ---------- */

  const renderStatus = (m: ModelInfo) => {
    if (m.isCurrent) return <span className="mm__chip mm__chip--current">当前使用</span>
    if (m.available) return <span className="mm__chip mm__chip--ok">可用</span>
    return (
      <span className="mm__chip mm__chip--warn" title="未配置对应 Key，调用时自动回落默认模型">
        未配 Key
      </span>
    )
  }

  const customCount = reg?.models.filter((m) => m.source === 'custom').length ?? 0

  return (
    <motion.div className="mm" variants={containerVariants} initial="hidden" animate="visible">
      <PageHeader
        title="模型管理"
        highlight="模型"
        subtitle="多模型接入 · 界面配置 API Key（加密存储）· 一键切换生成模型"
        onBack={() => onNavigate('dashboard')}
        crumb="模型管理"
        badges={[
          { label: '当前模型', value: reg?.models.find((m) => m.isCurrent)?.label ?? '—', tone: 'accent' },
          { label: '自建配置', value: customCount, tone: 'default' },
        ]}
        actions={
          <button className="btn btn--primary" onClick={openAdd}>
            + 添加模型
          </button>
        }
      />

      {!USE_REAL_API && (
        <motion.div className="mm__note glass-card" variants={itemVariants}>
          当前为前端 Mock 演示模式（未设 <code>VITE_USE_REAL_API=true</code>），本页操作仅在本地演示，
          连接后端后即为真实模型配置。
        </motion.div>
      )}

      {error && (
        <motion.div className="mm__error glass-card" variants={itemVariants}>
          {error}
        </motion.div>
      )}

      {loading ? (
        <motion.div className="mm__empty glass-card" variants={itemVariants}>
          模型列表加载中…
        </motion.div>
      ) : (
        <motion.div className="mm__grid" variants={itemVariants}>
          {(reg?.models ?? []).map((m) => (
            <div key={m.id} className={`mm__card glass-card ${m.isCurrent ? 'mm__card--current' : ''}`}>
              <div className="mm__card-head">
                <div className="mm__card-title">
                  <strong className="mm__label">{m.label}</strong>
                  {renderStatus(m)}
                </div>
                <span className={`mm__provider mm__provider--${m.provider}`}>
                  {PROVIDER_LABEL[m.provider] ?? m.provider}
                  {m.source === 'custom' ? ' · 自建' : ' · 内置'}
                </span>
              </div>

              <dl className="mm__meta">
                <div className="mm__meta-row">
                  <dt>model_id</dt>
                  <dd title={m.modelId}>{m.modelId || m.id}</dd>
                </div>
                {m.baseUrl && (
                  <div className="mm__meta-row">
                    <dt>base_url</dt>
                    <dd title={m.baseUrl}>{m.baseUrl}</dd>
                  </div>
                )}
                {m.source === 'custom' && (
                  <div className="mm__meta-row">
                    <dt>api_key</dt>
                    <dd className="mm__masked">{m.apiKeyMasked}</dd>
                  </div>
                )}
              </dl>

              <div className="mm__card-actions">
                {!m.isCurrent && (
                  <button
                    className="btn btn--primary mm__btn"
                    disabled={busyId === m.id}
                    onClick={() => void handleSwitch(m)}
                  >
                    {busyId === m.id ? '切换中…' : '设为当前'}
                  </button>
                )}
                {m.source === 'custom' && (
                  <>
                    <button
                      className="btn btn--ghost mm__btn"
                      disabled={busyId === m.id}
                      onClick={() => void handleCardTest(m)}
                    >
                      {busyId === m.id ? '测试中…' : '测试'}
                    </button>
                    <button className="btn btn--ghost mm__btn" disabled={busyId === m.id} onClick={() => openEdit(m)}>
                      编辑
                    </button>
                    <button
                      className={`btn btn--ghost mm__btn ${confirmDelId === m.id ? 'mm__btn--danger' : ''}`}
                      disabled={busyId === m.id}
                      onClick={() => void handleDelete(m)}
                    >
                      {confirmDelId === m.id ? '确认删除？' : '删除'}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </motion.div>
      )}

      <motion.p className="mm__hint" variants={itemVariants}>
        安全说明：API Key 提交后仅以加密形式存于服务端（Fernet），界面回显一律脱敏（仅后四位）；
        模型配置按账号隔离，仅本人可见可用。模型调用失败时自动回落默认 DeepSeek / 离线 Mock，生成链路不中断。
      </motion.p>

      {/* ===== 添加/编辑弹窗 ===== */}
      <AnimatePresence>
        {form && (
          <motion.div
            className="mm__mask"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closeForm}
          >
            <motion.div
              className="mm__panel glass-card"
              initial={{ opacity: 0, y: 24, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 12, scale: 0.98 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label={form.id ? '编辑模型配置' : '添加模型'}
            >
              <h3 className="mm__panel-title">{form.id ? '编辑模型配置' : '添加模型'}</h3>

              <label className="mm__field">
                <span>显示名</span>
                <input
                  type="text"
                  value={form.label}
                  placeholder="如：我的 GLM-4.6"
                  maxLength={64}
                  onChange={(e) => setForm((f) => (f ? { ...f, label: e.target.value } : f))}
                />
              </label>

              <label className="mm__field">
                <span>Provider 类型</span>
                <select value={form.provider} onChange={(e) => onProviderChange(e.target.value as FormState['provider'])}>
                  <option value="modelscope">魔搭 ModelScope（OpenAI 兼容）</option>
                  <option value="deepseek">DeepSeek（自有 Key）</option>
                  <option value="openai">其他 OpenAI 兼容端点</option>
                </select>
              </label>

              <label className="mm__field">
                <span>base_url</span>
                <input
                  type="text"
                  value={form.baseUrl}
                  placeholder="https://api-inference.modelscope.cn/v1"
                  onChange={(e) => setForm((f) => (f ? { ...f, baseUrl: e.target.value } : f))}
                />
              </label>

              <label className="mm__field">
                <span>model_id</span>
                <input
                  type="text"
                  value={form.modelId}
                  placeholder={form.provider === 'modelscope' ? '如：ZhipuAI/GLM-4.6' : '如：deepseek-chat'}
                  onChange={(e) => setForm((f) => (f ? { ...f, modelId: e.target.value } : f))}
                />
              </label>

              <label className="mm__field">
                <span>API Key</span>
                <input
                  type="password"
                  value={form.apiKey}
                  autoComplete="new-password"
                  placeholder={form.id ? `留空保留原 Key（${form.keyMasked ?? '****'}）` : '仅提交一次，服务端加密存储'}
                  onChange={(e) => setForm((f) => (f ? { ...f, apiKey: e.target.value } : f))}
                />
              </label>

              {form.provider === 'modelscope' && (
                <p className="mm__form-hint">
                  魔搭：填 base_url=https://api-inference.modelscope.cn/v1 + 魔搭访问令牌 + 模型 id 即可
                  （令牌在 modelscope.cn 个人中心生成）。
                </p>
              )}

              {formTest && (
                <p className={`mm__test-result ${formTest.ok ? 'mm__test-result--ok' : 'mm__test-result--fail'}`}>
                  {formTest.ok ? '✅' : '❌'} {formTest.message}
                </p>
              )}
              {formErr && <p className="mm__test-result mm__test-result--fail">{formErr}</p>}

              <div className="mm__panel-actions">
                <button
                  className="btn btn--ghost"
                  disabled={formBusy || !formTestable}
                  title="用当前填写的配置向上游发一次轻量请求验证连通"
                  onClick={() => void handleFormTest()}
                >
                  {formBusy ? '请求中…' : '测试连通'}
                </button>
                <div className="mm__panel-actions-right">
                  <button className="btn btn--ghost" disabled={formBusy} onClick={closeForm}>
                    取消
                  </button>
                  <button className="btn btn--primary" disabled={!formValid || formBusy} onClick={() => void handleFormSave()}>
                    {formBusy ? '保存中…' : form.id ? '保存修改' : '添加'}
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ===== 操作反馈 toast ===== */}
      <AnimatePresence>
        {toast && (
          <motion.div
            className="mm__toast glass-card"
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
