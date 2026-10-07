/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** P0 联调开关：'true' 时前端走真实后端 API，否则回退现有 mock */
  readonly VITE_USE_REAL_API?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
