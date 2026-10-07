/**
 * 会话级数据缓存（SWR：stale-while-revalidate）+ 在途请求去重。
 *
 * 面向「页面按导航重挂载、每次挂载重拉数据」的场景（如学情管家中枢）：
 * - 有缓存 → 立即回放上次数据（页面秒出），同时后台重拉，拿到新数据再刷新；
 * - 无缓存 → 正常请求，落缓存；
 * - 同 key 并发请求（含 StrictMode 双挂载）共享同一在途 Promise，不重复打后端。
 *
 * 缓存 key 由调用方自带用户/参数维度（如 `${userId}:evaluation`），登录切换后
 * 旧 key 自然不再命中，无需显式失效；数据新鲜度由「后台重拉」保证。
 */

const cache = new Map<string, unknown>()
const inflight = new Map<string, Promise<unknown>>()

/**
 * SWR 拉取：缓存命中先同步 apply 一次旧值，随后台请求完成再 apply 新值。
 * 返回的 Promise 在新数据 apply 后 resolve；请求失败时 reject（调用方自行 catch，
 * 已回放的缓存值仍然有效——降级为「显示上次数据」）。
 */
export function swrFetch<T>(key: string, fetcher: () => Promise<T>, apply: (data: T) => void): Promise<void> {
  if (cache.has(key)) apply(cache.get(key) as T)
  let p = inflight.get(key) as Promise<T> | undefined
  if (!p) {
    p = fetcher()
      .then((d) => {
        cache.set(key, d)
        return d
      })
      .finally(() => inflight.delete(key))
    inflight.set(key, p)
  }
  return p.then((d) => apply(d))
}
