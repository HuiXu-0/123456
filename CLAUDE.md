# CLAUDE.md — 智学中枢（LearnPilot）项目约定

## 项目概述

LearnPilot 是面向领域知识个性化资源生成与多智能体协同的学习平台。
- `frontend/`：React 18 + TS + Vite + Zustand + Framer Motion + GSAP + ECharts，**已完成**，当前全部由 mock 数据驱动，运行 `npm run dev`（端口 3001）。
- `backend/`：FastAPI + LangGraph + Chroma + SQLite，**按 docs/后端开发执行方案-v1.1.md 分阶段开发中**。
- `docs/`：需求与方案文档，所有开发依据均在此目录。

## 契约权威顺序（冲突时按此裁决）

1. `docs/后端接口文档.md`（30 接口，前端逆向，字段名严格对齐，禁止擅改）
2. `frontend/src/` 中的 TypeScript 类型定义
3. `docs/开发需求说明文档.md`（算法/架构依据；其 5.1 节老接口仅作评审映射，不实现）

## 后端工程纪律（每个阶段必须遵守）

1. **一次只做执行方案中的一个阶段（B0–B8），完成即停**，不顺手做下一阶段的事。
2. 每阶段新文件 ≤ 8 个；目录结构按需求文档 9.1 节 `backend/app/{api,agents,core,workflows,rag,services,models,schemas}`。
3. **统一响应信封** `{code, message, data, traceId}`，错误码按接口文档 1.3；WS/SSE 推送同样套信封。
4. **Mock-first**：所有 LLM 调用经 `app/core/llm.py` 的 `LLMClient` 适配层（provider 可配 mock/deepseek/qwen/anthropic）；Mock 模式按接口契约返回结构化假数据，**无任何 API Key 必须能跑通全链路**。
5. 轻量栈：Chroma（嵌入式）+ SQLite + bge 本地模型 + 内存 TTL 会话。不引入 Milvus/PostgreSQL/Redis/Neo4j（生产替换路径只写 README）。
6. 耗时操作走异步任务：`taskId` 状态机 `pending→running→succeeded/failed`，`GET /api/v1/tasks/{taskId}` 轮询。
7. 日志中的手机号/邮箱必须经脱敏拦截器掩码。

## 阶段收尾要求

- 输出完成总结写入 `docs/progress/<阶段号>.md`：文件清单 + 启动命令 + 验证命令与实测结果。
- 验证标准：接口类阶段贴 curl 实测回包；算法类阶段贴 pytest 输出；涉及前端联调时确认页面无回归。**0 报错才算完成。**
- 每阶段完成后提交一次 git commit，message 格式：`feat(backend): B<N> <阶段名>`。

## 常用命令

```bash
# 前端
cd frontend && npm run dev          # http://localhost:3001，/api 代理到 :8000

# 后端
cd backend && uvicorn app.main:app --reload --port 8000
cd backend && pytest -q

# 健康检查
curl http://localhost:8000/api/v1/health
```

## 禁止事项

- 禁止修改 `frontend/src` 的业务逻辑、Zustand store 结构、路由（联调切换 API 时仅允许改 `src/services/` 数据获取层，且需在总结中单独列出）。
- 禁止擅改接口文档中已定义的路径、字段名、枚举值。
- 禁止跳过 Mock 直接依赖真实 LLM Key 才能运行的实现。
- 禁止在未被要求时重构已验收阶段的代码。
