# 智学中枢 —— 领域知识个性化资源生成与多智能体系统

> 项目需求、接口契约、架构设计和部署说明见 [`docs/README.md`](docs/README.md)。

> 继续开发前请先阅读 [`docs/README.md`](docs/README.md) 和
> [`docs/维护/当前工程状态.md`](docs/维护/当前工程状态.md)。前者定义文档权威顺序，
> 后者记录当前实现、Mock/真实边界、已知风险和下一步。

面向「画像诊断 → 学习路径 → 资源生成 → 检验闭环」的个性化学习平台：

- **frontend/**：React 18 + TypeScript + Vite + Zustand + Framer Motion + GSAP + ECharts
- **backend/**：FastAPI + LangGraph（诊断 / 生成 / 审核三 Agent）+ Chroma + SQLite + bge 本地模型
- **docs/**：需求文档、接口契约、算法与架构设计、部署说明

```
浏览器 ── Vite dev(:3001, /api 代理) ──► FastAPI(:8000)
                                          ├─ LangGraph 工作流（diagnostic → retrieval → generation → critic）
                                          ├─ RAG：chunker → bge-small-zh → Chroma（BM25+向量 RRF → bge-reranker）
                                          ├─ SQLite（用户/知识点/掌握度/缓存/快照） + 内存 TTL 会话/任务
                                          └─ LLMClient 适配层（mock / deepseek 可切换）
```

## 一、一键启动

### 后端（无任何 API Key 即可跑通全链路）

```bash
cd backend
pip install -r requirements.txt
python -m app.core.init_db                      # 幂等建表 + 种子（可重复执行）
uvicorn app.main:app --reload --port 8000
# 健康检查：curl http://localhost:8000/api/v1/health
```

> 首次调用 RAG 相关接口时自动加载本地 bge 模型（已缓存于 `backend/data/models/`）；
> 加载失败自动降级为哈希嵌入，链路不中断。

### 前端

```bash
cd frontend
npm install
npm run dev                                     # http://localhost:3001，/api 代理到 :8000
```

联调真实后端：在 `frontend/.env` 写入 `VITE_USE_REAL_API=true`（默认 false 走本地 mock，断网可演示）。

### 种子账号

| 账号 | 密码 | 角色 |
|---|---|---|
| `learner_001` | `123456` | learner（学习者工作台） |
| `admin` | `admin123` | admin（管理端：知识库 / Prompt / 指标看板） |

### 知识库灌库（可选）

```bash
cd backend && python seed_kb.py                 # 导入 seed_docs/*.md（切片→向量化→Chroma）
```

## 二、Mock / DeepSeek 切换

所有 LLM 调用统一经 `backend/app/core/llm.py` 的 `LLMClient` 适配层（`backend/.env`）：

```bash
# 演示兜底（默认）：确定性结构化假数据，零网络、零 Key
LLM_PROVIDER=mock

# 真实生成：画像抽取 / 叙述 / 讲义工作流（RAG 检索→生成→逐句接地审核）/ 强化 / 辅导
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
```

- 讲义缓存按 provider 隔离（`lecture` 与 `lecture@deepseek`），切换互不污染；
- 真实模式 LLM 异常统一映射 `code 2001`，前端有兜底提示；
- `qwen` / `anthropic` 为适配层预留位（未实现）。

## 三、环境开关一览（backend/.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` / `deepseek`（见上） |
| `DEEPSEEK_API_KEY` | — | deepseek 模式必填 |
| `JOB_MARKET_OFFLINE` | `false` | `true` 模拟岗位数据源故障 → 接口返回 `code 2002 + data.offline=true`（前端「离线快照」降级演示） |
| `GROUNDING_THRESHOLD` | `0.75` | 15.3 逐句接地阈值（bge-small-zh 实测标定 0.6） |
| `WORKFLOW_STEP_DELAY_MS` | `500` | 工作流大屏节点推进延迟（演示节奏；测试置 0） |
| `TUTOR_STREAM_DELAY_MS` | `50` | 苏格拉底辅导 SSE 打字机字间延迟 |
| `DATABASE_URL` | `sqlite:///zhixue.db` | 数据库连接串 |
| `CHROMA_PERSIST_DIR` / `MODEL_CACHE_DIR` | `backend/data/*` | 向量库 / bge 模型缓存目录 |
| `JWT_SECRET` / `JWT_EXPIRE_SECONDS` | — / `7200` | JWT 签名与有效期 |

前端：`VITE_USE_REAL_API`（`true` 联调后端 / `false` 本地 mock）。

## 四、需求文档 5.1 老接口 → 实际 30 接口映射

需求文档 5.1 的 4 个粗粒度接口在前端逆向契约（`docs/后端接口文档.md`）中拆分为 30 个
细粒度接口（+管理端 31–36），语义映射如下：

| 需求文档 5.1（评审口径） | 实际实现（接口文档编号） |
|---|---|
| `POST /user/profile` 用户画像 | 4 `POST /profile/parse`（多模态解析）· 5 `POST /profile/narrative`（接地叙述）· 6 `POST /profile/diagnosis-complete` · 7 `GET /profile/ability-portrait`（雷达） |
| `POST /generator/learning-package` 资源生成 | 17 `POST /resource/lecture`（自适应讲义+SourceTrace+幻觉率）· 18 `POST /resource/video` · 19/20 `GET /resource/mindmap|diagram/{kpId}` · 21 `GET /resource/external/{kpId}` · 23 `GET /quiz/{kpId}` · 11 `POST /learning-path/generate` · 27/28 `POST /workflow/execute` + `GET|WS /workflow/{id}`（多智能体可视化） |
| `POST /feedback/submit` 动态反馈 | 24 `POST /quiz/{kpId}/submit`（判分+掌握度联动）· 25 `POST /reinforce`（错题强化）· 13/14 `POST /mastery/{kpId}/check|pass` |
| `GET /report/visualization` 可视化数据 | 29 `GET /dashboard/overview`（学情聚合）· 26 `GET /knowledge-graph` · 10 `GET /learning-path` · 15 `GET /journey` · 8/9 `GET /job-market/*`（岗位对标）· 36 `GET /admin/metrics`（指标看板） |

其余：1–3 Auth、12 掌握度全集、16 知识点元信息、22 苏格拉底辅导（SSE）、30 异步任务、
31–35 管理端知识库/Prompt 热更新。字段级契约见 `docs/后端接口文档.md` 第 13 章总览表。

## 五、生产替换路径（demo 轻量栈 → 生产栈）

| 组件 | demo（当前） | 生产替换 | 改造点 |
|---|---|---|---|
| 向量库 | Chroma（嵌入式持久化） | Milvus / Pinecone | `app/rag/vector_store.py` 单文件适配层，接口（upsert/search/delete）不变 |
| 关系库 | SQLite | PostgreSQL + 连接池 | 仅改 `DATABASE_URL`（SQLAlchemy ORM 全程无方言耦合）+ Alembic 迁移 |
| 任务/会话 | 进程内存（TTL） | Redis + Celery | `app/core/tasks.py` / tutor 会话 / JWT 黑名单三处内存表换 Redis |
| 嵌入/重排 | bge 本地 CPU 推理 | 独立模型服务（vLLM/TEI） | `app/rag/embeddings.py`、`reranker.py` 改 HTTP 客户端 |
| LLM | DeepSeek（openai 兼容） | 企业网关（配额/审计/多模型路由） | `app/core/llm_deepseek.py` 换 base_url 即可 |
| 视频 TTS | 前端 Remotion Player + 浏览器 SpeechSynthesis | 服务端 Remotion 渲染 mp4 + 云 TTS（如 CosyVoice/Azure TTS），回填 `videoUrl` | 接口 8.3 契约已预留 `videoUrl: null → mp4 url` |
| 外部资源聚合 | `ExternalResource` 精选种子库 | 聚合 Agent 调 YouTube Data API / B 站 / arXiv 搜索 API 检索 → critic 评分过滤入库 | `app/services/resource.py::external_resources` 签名不变 |
| 岗位市场 | `JobSnapshot` 预置快照 | 离线采集管线（BOSS直聘/拉勾/智联公开 JD 样本）+ LLM 抽取技能频率，定时刷新快照 | 接口 5.1/5.2 签名不变，降级协议（2002）已就绪 |

## 六、测试与量化指标

Windows 一键后端验证（默认全量 Mock 测试，无 API Key、无网络调用）：

```powershell
.\scripts\verify-backend.bat
# 首次安装开发依赖：.\scripts\verify-backend.bat -Install
# Python 未加入 PATH：.\scripts\verify-backend.bat -Python C:\path\to\python.exe
# 只跑契约测试：.\scripts\verify-backend.bat -Tests tests/test_contract_snapshot.py
```

开发/测试依赖单独固定在 `backend/requirements-dev.txt`；生产依赖仍使用 `backend/requirements.txt`。

```bash
cd backend
python -m pytest -q                              # 全量回归（含 app 内置测试；mock 基线，无 Key 可跑）
python -m pytest tests/test_contract_snapshot.py -q   # 核心接口运行时字段契约（防字段漂移）
python scripts/export_openapi_snapshot.py --check     # 83 个 HTTP 操作 + Schema 快照（只校验不覆盖）

python scripts/metrics/make_report.py            # 三量化指标 → docs/metrics-report.md
python scripts/metrics/hallucination_rate.py     # ① 幻觉率（逐句接地，目标 <5%）
python scripts/metrics/difficulty_adaptation.py  # ② 难度适配率（三档区分度，目标 ≥85%）
python scripts/metrics/knowledge_coverage.py     # ③ 知识覆盖率（核心概念命中，目标 ≥90%）

python scripts/demo_profiles.py                  # 3 组差异化画像种子（答辩演示素材）
```

最近一次指标实测见 `docs/metrics-report.md`，并已接入管理端指标看板
（`GET /api/v1/admin/metrics`）。开发阶段记录见 `docs/progress/`（B0–B8）。
