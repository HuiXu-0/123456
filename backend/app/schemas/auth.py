"""Auth 请求体 schema（B1）。

仅约束入参；响应统一经 app.core.envelope.success 包裹为信封，故响应体用普通 dict
按接口文档 3.1 / 15.1 拼装（含 camelCase 字段名），不在此定义响应模型。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    remember: bool = False  # 可选


class RegisterRequest(BaseModel):
    """注册请求（接口文档 16.1）。强度/查重校验在服务层（返回业务码 1001），
    此处仅约束非空——空串由 min_length 触发 422→1001，与服务层口径一致。"""

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """修改密码请求（接口文档 3.4）。需登录态；旧密码校验与新密码强度校验在服务层
    （返回业务码 1001），此处仅约束非空——空串由 min_length 触发 422→1001。"""

    oldPassword: str = Field(min_length=1)
    newPassword: str = Field(min_length=1)
