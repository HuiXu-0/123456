"""生成模型管理接口（接口文档 21，additive 新增，不改任何既有接口）。

- GET    /models                 注册表（内置 + 本人自建）+ 当前模型（21.1，字段 additive 扩展）
- PUT    /models/current         切换当前生成模型（21.2；自建配置 → 仅本人生效）
- POST   /models/configs         新增自建模型配置（21.3，key 加密落库、脱敏返回）
- PUT    /models/configs/{id}    编辑本人配置（21.3；api_key 缺省 = 保留原 key）
- DELETE /models/configs/{id}    删除本人配置（21.3；若为当前 → 回落默认）
- POST   /models/configs/test    测试连通性（21.4，轻量上游调用，失败信息已脱敏）

隔离语义：自建配置按 user 隔离（读写/使用均校验归属，非本人 → 1004）；内置模型
切换维持既有进程级运行态语义（§21.2 向后兼容）。默认 = 既有 DeepSeek，
不切换则默认行为不变；调用失败经 llm_transport 自动回落（绝不崩）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import llm_userconf, model_registry
from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.security import get_current_user
from app.models.entities import User
from app.services import model_configs as svc
from app.services.model_configs import ModelConfigError

router = APIRouter(tags=["models"])


class ModelSwitchRequest(BaseModel):
    modelId: str = Field(..., min_length=1)


class ModelConfigCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    provider: str = Field(...)  # openai | modelscope | deepseek
    baseUrl: str = Field(..., min_length=1, max_length=256)
    modelId: str = Field(..., min_length=1, max_length=128)
    apiKey: str = Field(..., min_length=1)


class ModelConfigUpdateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    provider: str = Field(...)
    baseUrl: str = Field(..., min_length=1, max_length=256)
    modelId: str = Field(..., min_length=1, max_length=128)
    apiKey: str | None = None  # 空/缺省 = 保留原 key


class ModelTestRequest(BaseModel):
    configId: str | None = None  # 测已保存配置（可被下方表单值覆盖）
    provider: str | None = None  # 仅作提示，不影响通道（均 OpenAI 兼容）
    baseUrl: str | None = None
    modelId: str | None = None
    apiKey: str | None = None


@router.get("/models")
async def list_models(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """可选生成模型列表 + 当前模型（接口文档 21.1；含本人自建配置，key 脱敏）。"""
    return success(model_registry.user_snapshot(db, user.id))


@router.put("/models/current")
async def switch_model(
    body: ModelSwitchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """切换当前生成模型（接口文档 21.2）。返回切换后的注册表快照。

    - 目标为本人自建配置 → 写用户级 overlay（仅本人后续生成走它，A/B 隔离）；
    - 目标为内置模型 → 既有进程级运行态语义 + 清本人 overlay（向后兼容）。
    """
    if svc.set_user_current(db, user.id, body.modelId):
        return success(model_registry.user_snapshot(db, user.id))
    try:
        model_registry.set_current(body.modelId)
    except KeyError:
        return fail(code=1001, message=f"未知模型：{body.modelId}", status_code=400)
    svc.clear_user_current(db, user.id)
    return success(model_registry.user_snapshot(db, user.id))


@router.post("/models/configs")
async def create_model_config(
    body: ModelConfigCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新增自建模型配置（接口文档 21.3）。api_key 加密落库，返回脱敏形。"""
    try:
        data = svc.create_config(
            db,
            user.id,
            label=body.label,
            provider=body.provider,
            base_url=body.baseUrl,
            model_id=body.modelId,
            api_key=body.apiKey,
        )
    except ModelConfigError as e:
        return fail(code=e.code, message=e.message, status_code=e.status_code)
    return success(data)


@router.post("/models/configs/test")
async def test_model_config(
    body: ModelTestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试连通性（接口文档 21.4）：轻量上游调用，返回 {ok, latencyMs, message}。

    测试动作总是「执行成功」（code 0），连通结果在 data.ok；失败信息已做 key 脱敏。
    """
    try:
        target = svc.resolve_test_target(
            db,
            user.id,
            config_id=body.configId,
            base_url=body.baseUrl,
            model_id=body.modelId,
            api_key=body.apiKey,
        )
    except ModelConfigError as e:
        return fail(code=e.code, message=e.message, status_code=e.status_code)
    result = llm_userconf.probe(
        base_url=target["base_url"],
        api_key=target["api_key"],
        model_id=target["model_id"],
        label=target["label"],
    )
    return success(result)


@router.put("/models/configs/{config_id}")
async def update_model_config(
    config_id: str,
    body: ModelConfigUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """编辑本人配置（接口文档 21.3）。apiKey 空/缺省 = 保留原 key。"""
    try:
        data = svc.update_config(
            db,
            user.id,
            config_id,
            label=body.label,
            provider=body.provider,
            base_url=body.baseUrl,
            model_id=body.modelId,
            api_key=body.apiKey,
        )
    except ModelConfigError as e:
        return fail(code=e.code, message=e.message, status_code=e.status_code)
    llm_userconf.reset_clients()  # key/base_url 可能已变 → 丢弃旧客户端缓存
    return success(data)


@router.delete("/models/configs/{config_id}")
async def delete_model_config(
    config_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除本人配置（接口文档 21.3）。若为本人当前模型 → 清 overlay 回落默认。"""
    try:
        svc.delete_config(db, user.id, config_id)
    except ModelConfigError as e:
        return fail(code=e.code, message=e.message, status_code=e.status_code)
    return success({"deleted": config_id})
