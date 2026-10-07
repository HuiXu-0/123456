"""测试全局基线（B5-b）+ 测试库隔离（根治"共享脏库累积"偶发失败）。

backend/.env 在真实演示环境可能配置 LLM_PROVIDER=deepseek + 真实 Key；
测试套件必须与环境无关：一律以 mock provider 为基线（确定性、零网络、零计费），
deepseek 路径用例自行构造 LLMClient("deepseek") 并 monkeypatch llm_deepseek.chat
（见 test_b5b.py 的 deepseek_llm fixture），不受本基线影响。

测试库隔离：每次 pytest 进程用一次性临时 SQLite 库，跑完整目录删除。此前测试
套件直接跑在持久化 dev 库（backend/zhixue.db）上，注册类用例（对话画像 dlg_*、
B9 注册等）逐次累积用户，最终把 16.2 用户列表撑过单页窗口，导致 test_38 等用例
偶发失败。隔离后每次运行从干净种子库起步，根治所有共享脏库累积问题。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# ── 测试库隔离：必须在任何 app 模块导入【之前】指向临时库 ─────────────────────
# app.core.database 在 import 时即按 settings.database_url 建 engine，而 settings
# 读 env(DATABASE_URL) 优先于 .env。故此处最先把库指向进程级一次性临时文件——
# 下方 app.* 导入（含 torch warmup 链）随即按该路径建 engine。
_TEST_DB_DIR = tempfile.mkdtemp(prefix="zhixue_test_db_")
_TEST_DB_PATH = Path(_TEST_DB_DIR) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"

# Windows 已知问题（B8）：torch DLL 在非主线程首次加载会 access violation 崩溃进程。
# TestClient 的请求处理都在 portal 线程进行，kb/RAG 模块的惰性导入会在该线程触发
# torch 导入 → 在 pytest 主线程先行导入 embeddings（连带 sentence_transformers/torch），
# 之后任何线程再 import 仅命中 sys.modules 缓存，不再加载 DLL。
from app.rag import embeddings as _warmup_embeddings  # noqa: F401,E402  isort: skip

from app.core import llm as llm_mod  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.init_db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_db():
    """会话级隔离库：建表 + 幂等种子（仅 admin/learner_001 等种子）；结束删库。"""
    init_db()
    yield
    # 先释放引擎连接再删目录（Windows 下文件被占用无法删除）
    from app.core.database import engine

    engine.dispose()
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _force_mock_provider():
    """整个测试会话强制各 provider 为离线/mock 默认，隔离本地 .env，保证可复现。

    本地 backend/.env 可能注入 LLM_PROVIDER=deepseek、SEARCH_PROVIDER=tavily(+Key) 等真实
    配置；测试套件必须与环境无关（确定性、零网络、零计费）。故此处把所有会改变测试行为的
    provider 类配置统一压回离线默认：
    - llm_provider → mock（并重置 LLM 单例）；
    - search_provider → none、search_api_key → 空（联网搜索回落种子兜底，web_search 每次
      调用即读 settings，无需额外重置单例）。
    deepseek 路径用例自行构造 LLMClient("deepseek") 并 monkeypatch，不受本基线影响。
    """
    original = {
        "llm_provider": settings.llm_provider,
        "search_provider": settings.search_provider,
        "search_api_key": settings.search_api_key,
        "video_render_enabled": settings.video_render_enabled,
    }
    settings.llm_provider = "mock"
    settings.search_provider = "none"
    settings.search_api_key = ""
    # 视频服务端渲染默认关：测试与环境无关、确定性、零网络（edge-tts）、零子进程（node 渲染）。
    # 渲染产物属增强项，关闭后 /resource/video.videoUrl 维持 null、/resource/video/render 回
    # unavailable——既验证降级契约，又不让 10-15s 的渲染拖慢/污染测试。
    settings.video_render_enabled = False
    llm_mod._client = None
    yield
    settings.llm_provider = original["llm_provider"]
    settings.search_provider = original["search_provider"]
    settings.search_api_key = original["search_api_key"]
    settings.video_render_enabled = original["video_render_enabled"]
    llm_mod._client = None
