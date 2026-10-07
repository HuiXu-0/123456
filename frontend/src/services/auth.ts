/**
 * 认证数据获取层（接口文档第 3 章）。登录成功后落 token + user 到 localStorage，
 * 后续请求由 api.ts 自动附带 Authorization。
 */
import { USE_REAL_API, apiPost, clearAuth, setToken, setUser, type AuthUser } from './api'

interface LoginResponse {
  token: string
  expiresIn: number
  user: AuthUser
}

/** 登录（接口文档 3.1 + 15.1）。成功落 token/user 并返回 user。 */
export async function login(username: string, password: string, remember = true): Promise<AuthUser> {
  const data = await apiPost<LoginResponse>('/auth/login', { username, password, remember })
  setToken(data.token)
  setUser(data.user)
  return data.user
}

/**
 * 注册（接口文档 16.1）。成功即自动登录：落 token/user 并返回 user
 * （role=learner、hasDiagnosed=false）。用户名已存在 / 密码过弱由后端返回
 * code 1001（抛 ApiError，调用方据 message 提示）。
 */
export async function register(username: string, password: string): Promise<AuthUser> {
  const data = await apiPost<LoginResponse>('/auth/register', { username, password })
  setToken(data.token)
  setUser(data.user)
  return data.user
}

/**
 * 修改密码（接口文档 3.4）。需登录态；旧密码错误 / 新密码过弱后端共用 code 1001，
 * 调用方据抛出的 ApiError.message（后端文案）区分提示，前端不臆测。
 *
 * Mock-first：无后端（USE_REAL_API=false）时模拟成功，保证离线演示可走通表单交互；
 * 「旧密码错误」等服务端校验需联调真实后端方可触发。
 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  if (!USE_REAL_API) {
    await new Promise((r) => setTimeout(r, 400))
    return
  }
  await apiPost('/auth/change-password', { oldPassword, newPassword })
}

/** 退出（接口文档 3.3）。无论后端是否成功都清本地登录态。 */
export async function logout(): Promise<void> {
  try {
    await apiPost('/auth/logout')
  } catch {
    /* 退出失败不阻断本地清票 */
  }
  clearAuth()
}
