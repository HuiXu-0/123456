"""应用配置（pydantic-settings）。

读取 backend/.env（可选），所有字段均带默认值——无 .env、无密钥也能启动。
后续阶段（B1+）在此追加数据库 / LLM 等配置项。
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 允许 model_cache_dir / model_xxx 等字段名（默认 model_ 为 pydantic 保护命名空间）
        protected_namespaces=(),
    )

    # 应用元信息
    app_name: str = "智学中枢后端"
    api_prefix: str = "/api/v1"

    # CORS 允许来源（默认放行前端 Vite 3000/3001）
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # 统一日志：默认保持适合本地开发的文本输出，可经环境变量调整。
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    log_request_completed: bool = True

    # LLM Provider（mock / deepseek / qwen / anthropic）
    llm_provider: str = "mock"

    # DeepSeek（B5-b 真实生成，OpenAI 兼容协议）：Key 经 backend/.env 注入，
    # 缺省为空——mock 模式无任何 Key 必须能跑通全链路（CLAUDE.md 纪律）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_temperature: float = 0.3

    # 模型管理（界面切换生成模型，接口文档 21 additive）：接入魔搭 ModelScope API-Inference。
    # 魔搭推理 API 兼容 OpenAI 协议——复用现有 OpenAI SDK 调用方式，仅需 base_url +
    # MODELSCOPE_API_KEY + 模型名（app/core/llm_modelscope.py）。
    # - modelscope_api_key：魔搭访问令牌（modelscope.cn 个人中心生成，经 .env 注入）；
    #   缺省为空 → 魔搭模型标记「未配 Key」，调用时自动回落默认 DeepSeek / mock（绝不崩）；
    # - modelscope_models：注册表中的魔搭在线模型清单（逗号分隔 model_id，可经 .env 覆盖）。
    # 「当前模型」运行态见 app/core/model_registry.py，默认 = 既有 DeepSeek，默认行为不变。
    modelscope_api_key: str = ""
    modelscope_base_url: str = "https://api-inference.modelscope.cn/v1"
    modelscope_models: str = "ZhipuAI/GLM-4.6,Qwen/Qwen3-32B,deepseek-ai/DeepSeek-V3.1"

    # 模型管理·用户自建模型配置（接口文档 21.3+）：api_key 对称加密落库（Fernet）。
    # - model_key_secret：加密密钥材料（生产必须经 .env 配置专用随机串）；缺省为空 →
    #   从 jwt_secret 派生（零配置可跑，demo 兜底；README 注明生产要求）。
    # - model_test_timeout_seconds：「测试连通性」单次上游调用超时（短于生成超时，
    #   让界面快速得到成功/失败反馈）。
    model_key_secret: str = ""
    model_test_timeout_seconds: float = 20.0

    # 内容安全过滤（app/core/content_safety.py）：所有 LLM 生成文本返回前统一过滤。
    # - enabled：总开关（默认开）；
    # - model_check：可选的模型级二次校验（真实 provider 下用 LLMClient 通道做轻量
    #   违规分类补漏，失败回落词表；mock / 无 Key 自动不生效，默认关，避免拖慢链路）；
    # - lexicon_path：可选扩充词库（每行 `类别:词`，类别取 political|porn|
    #   violence_terror|illegal_harmful|abuse_discrimination），缺省仅用内置种子词表。
    content_safety_enabled: bool = True
    content_safety_model_check: bool = False
    content_safety_lexicon_path: str = ""

    # 15.3 逐句接地阈值：句子与来源切片最大 embedding 相似度低于该值 → 未接地。
    # 默认 0.6 为 bge-small-zh-v1.5 实测标定值（B5-b：逐句相似度中位数 ≈0.73，
    # 接地句多落 0.6-0.9，文档示例 0.75 会把 ~62% 真实接地句误判为幻觉）；
    # 按 15.3「阈值可在配置中调整」，.env 经 GROUNDING_THRESHOLD 可覆盖。
    grounding_threshold: float = 0.6

    # 数据库（B1）：SQLite 嵌入式，相对 backend/ 工作目录
    database_url: str = "sqlite:///./zhixue.db"

    # JWT（B1）：HS256；demo 默认密钥，生产经 .env 覆盖
    jwt_secret: str = "zhixue-dev-secret-change-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 7200  # 登录响应 expiresIn 与之一致

    # RAG 管道（B3）：本地模型 + Chroma 持久化，均带默认值（无网络/无模型也降级可跑）
    # 向量库 / 模型缓存目录（相对 backend/ 工作目录）
    chroma_dir: str = "./data/chroma"
    model_cache_dir: str = "./data/models"
    # 本地模型名（sentence-transformers 自动下载到 model_cache_dir；加载失败自动降级）
    embedding_provider: str = "sentence-transformers"
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512
    embedding_allow_fallback: bool = True
    embedding_evaluation_require_real: bool = False
    reranker_model_name: str = "BAAI/bge-reranker-base"
    # 降级哈希嵌入维度（embedding 模型不可用时启用，保证全链路可跑）
    # 向后兼容旧环境变量；新基础设施统一使用 embedding_dimension，禁止降级时改变维度。
    embedding_fallback_dim: int = 256
    # 切片参数（需求文档 4.3.1）
    chunk_size: int = 512
    chunk_overlap: int = 64
    # 混合检索 RRF 权重（需求文档 4.3.2）
    rrf_dense_weight: float = 0.7
    rrf_sparse_weight: float = 0.3
    rrf_k: int = 60
    retrieval_candidate_top_k: int = 20
    retrieval_final_top_k: int = 5
    retrieval_max_chunks_per_source: int = 2
    retrieval_min_dense_score: float = 0.35
    retrieval_min_query_overlap: float = 0.35
    retrieval_min_strong_keyword_overlap: float = 0.60
    retrieval_confidence_dense_weight: float = 0.10
    retrieval_confidence_keyword_weight: float = 0.05
    retrieval_confidence_fusion_weight: float = 0.85

    # B7-a 实时通道演示参数：
    # - workflow_step_delay_ms：工作流节点间推进延迟，让 WS / 轮询能观察到 phase
    #   渐进点亮（mock 工作流毫秒级完成，无延迟时大屏一闪而过）；0 = 不延迟（测试用）
    # - tutor_stream_delay_ms：tutor mock 逐字流式的字间延迟（打字机演示效果）
    workflow_step_delay_ms: int = 500
    tutor_stream_delay_ms: int = 20

    # R2：LangGraph 断点续跑（可选能力，默认关闭）。
    # - 关闭（默认）：graph.compile() 不带 checkpointer，**运行语义与 R1 前逐字一致**；
    # - 开启：State 快照落 SQLite（见 app/core/checkpoint.py），支持跨进程恢复/重放。
    #   State 已禁止承载 Session/Retriever/密钥，正是为了开启时可序列化。
    # - 即使开启，checkpointer 构建失败也只告警并回落无检查点路径，不打断业务。
    workflow_checkpoint_enabled: bool = False
    workflow_checkpoint_path: str = "./data/workflow_checkpoints.sqlite"

    # R3：Supervisor 的 L2 LLM 辅助路由（**默认关闭**，环境变量 SUPERVISOR_LLM_ROUTING）。
    # - 关闭（默认）：只走 L1 确定性规则路由，零 LLM 调用、零不可控行为；
    # - 开启：当前仍只留接线点（未接真实 LLM），会记 warning 并回落 L1 ——
    #   即"打开开关也不会失控"。L1 命中时 L2 根本不参与，故 LLM 无法覆盖规则。
    supervisor_llm_routing: bool = False

    # R3.5：Supervisor 生产入口灰度开关（环境变量 SUPERVISOR_ENABLED，**默认关闭**）。
    # - false（默认）→ workflow_runner 走 run_learning_workflow()，**行为与改造前逐字一致**；
    # - true         → 走 run_router_workflow()（Supervisor → Router → 子图）；
    # 每次调用现读本设置（非启动时快照），故改 .env 后**不重启即生效**，可随时回滚。
    # 影响范围仅 services/workflow_runner（POST /workflow/execute）；
    # services/resource.py 与消融脚本不读此开关（灰度批次 B，见 R3.5 报告）。
    supervisor_enabled: bool = False

    # R4.4：Tool Loop 生产接入开关（环境变量 AGENT_TOOL_LOOP_ENABLED，**默认关闭**）。
    # - false（默认）→ generation / critic **完全走旧路径**，与接入前逐字一致；
    # - true         → 两节点先跑 Tool Loop **取证**，再把证据并入既有
    #                   run_generator / run_critic 的入参；
    # **产出仍由既有函数产生**（markdown / passed / validationScore / hallucinationRate /
    # issues 的结构与算法不变），因此开关只影响"喂进去的证据"，不影响输出契约。
    # 每次 workflow 调用现读本设置（非启动快照）→ 改 .env 不重启即生效、可随时回滚。
    agent_tool_loop_enabled: bool = False

    # 岗位市场（B6 / 接口文档 15.5）：预置快照 JSON 目录（种子导入来源，相对 backend/）；
    # job_market_offline=True 模拟「实时数据源不可用」→ /job-market/{id} 走 2002 降级
    job_market_dir: str = "../frontend/public/data/job-market"
    job_market_offline: bool = False
    # 超过 12 小时的岗位快照仍返回，但必须按 code=2002/offline=true 明示陈旧。
    job_market_max_age_hours: float = 12.0
    # 可选可信采集器 JSON feed；仅由显式刷新命令调用，应用启动不主动联网。
    job_market_feed_url: str = ""
    job_market_feed_token: str = ""
    job_market_timeout_seconds: float = 12.0

    # 外部资源联网搜索（接口文档 8.6 增量，C-fix 批3-bonus）：可插拔搜索 provider。
    # - search_provider：none（无搜索能力，走种子兜底/offline）| tavily（Tavily Web Search API）；
    #   未来可扩展 serpapi / bing / youtube / arxiv，接口签名不变。
    # - search_api_key：对应 provider 的密钥（经 .env 注入；缺省为空 → 自动回落 offline 兜底）。
    # 缺省 none，无密钥也能跑（mock/种子兜底，CLAUDE.md 纪律）。
    search_provider: str = "none"
    search_api_key: str = ""
    search_base_url: str = "https://api.tavily.com"
    search_timeout_seconds: float = 12.0
    search_max_results: int = 8
    external_resource_cache_ttl_seconds: int = 12 * 60 * 60

    # 讲义真实配图「图片搜索 provider」（app/services/image_search.py）：与上面的文本搜索
    # （Tavily 等）解耦，专走**免版权、URL 稳定、可标注来源**的图源，替换掉之前 Tavily 图片那条
    # 带防盗链 / 会过期 CDN 链接（byteimg）导致「图片暂不可用」的路。
    # - image_provider：none（不插真实图）| wikimedia（默认，Wikimedia Commons，免密钥）| pexels（需 Key）；
    # - wikimedia_api_url：Commons MediaWiki API 入口（免密钥，联网即可用）；
    # - pexels_api_key / pexels_base_url：预留兜底图源（配 Key 即作为 Wikimedia 没命中时的 fallback）；
    # - image_search_timeout_seconds / image_search_max_results：单次搜图超时与候选条数。
    # 缺省 wikimedia：真实 LLM 模式联网取真实图；mock 模式由 lecture_media 拦截走占位、不调本模块
    # （无任何 Key 仍跑通全链路，CLAUDE.md 纪律）。
    image_provider: str = "wikimedia"
    wikimedia_api_url: str = "https://commons.wikimedia.org/w/api.php"
    pexels_api_key: str = ""
    pexels_base_url: str = "https://api.pexels.com"
    image_search_timeout_seconds: float = 12.0
    image_search_max_results: int = 8

    # 语音合成 TTS（讲解视频/导学旁白配音，移植自桌面端 voice.py 的合成逻辑，Web 化）：
    # - tts_provider：edge（默认，edge-tts 微软神经语音，联网免密钥）| none（禁用 → 前端回落浏览器 TTS）；
    # - tts_voice / tts_rate / tts_pitch：默认自然中文女声 XiaoyiNeural（语速 -10%、音调 +2Hz）；
    # - tts_cache_dir：以 md5(text+voice+rate+pitch) 为键缓存 MP3，命中直接返回（目录入 .gitignore）；
    # - tts_timeout / tts_max_retries：单次合成超时与重试兜底；联网失败优雅降级（返回 2002，不崩）。
    # 缺省 edge，但无网/合成失败自动降级，Mock/无密钥仍跑通全链路（CLAUDE.md 纪律）。
    tts_provider: str = "edge"
    tts_voice: str = "zh-CN-XiaoyiNeural"
    tts_rate: str = "-10%"
    tts_pitch: str = "+2Hz"
    tts_cache_dir: str = "./data/tts_cache"
    tts_timeout_seconds: float = 20.0
    tts_max_retries: int = 3
    tts_max_chars: int = 2000  # 单次合成文本上限（防超长滥用）

    # 讲解视频服务端渲染（mp4）——增强项，非必需：拿不到 mp4 前端回落实时 Player+TTS。
    # - video_render_enabled：总开关；关 / 无渲染能力（Node/依赖/无头浏览器缺失）→ videoUrl 维持 null、降级；
    # - video_render_dir：渲染工作根目录（bundle 缓存 + 各段 TTS 临时音频 + out/*.mp4 产物，均入 .gitignore）；
    # - video_frontend_dir：Remotion 渲染脚本 + node_modules 所在前端目录（相对 backend/ 工作目录）；
    # - video_render_browser：无头浏览器可执行文件；留空时自动探测复用本机 Playwright chrome-headless-shell；
    # - video_node_bin：Node 可执行文件名/路径；video_render_timeout_seconds：单次渲染子进程超时。
    video_render_enabled: bool = True
    video_render_dir: str = "./data/video"
    video_frontend_dir: str = "../frontend"
    video_render_browser: str = ""
    video_node_bin: str = "node"
    video_render_timeout_seconds: float = 300.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """允许 .env 用逗号分隔字符串配置 CORS_ORIGINS。"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return normalized

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"text", "json"}:
            raise ValueError("LOG_FORMAT must be text or json")
        return normalized


settings = Settings()
