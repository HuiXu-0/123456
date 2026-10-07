"""认证与鉴权（B1）。

- 口令哈希：stdlib `hashlib.pbkdf2_hmac`（纯 Python，无 bcrypt 原生构建，Windows 友好）；
- 令牌：PyJWT HS256，载荷含 sub / role / jti / exp；
- 退出登录：内存 jti 黑名单（轻量栈，生产替换 Redis，路径写 README）；
- 依赖：get_current_user（无/失效 token → 401→1002）、require_admin（非管理员 → 403→1003）。

错误经 FastAPI HTTPException 抛出，由 main.py 的异常处理器套统一信封（B0 已建）：
401 → code 1002、403 → code 1003。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import get_db
from app.models.entities import User

_PBKDF2_ITERATIONS = 100_000

# 退出登录后失效的令牌 jti（进程内存；生产替换 Redis）
_revoked_jti: set[str] = set()

# auto_error=False：缺省凭据时不自动抛 403，交由我们抛 401（对齐契约：无 token → 1002）
_bearer = HTTPBearer(auto_error=False)


# ---- 口令哈希 -------------------------------------------------------------

def hash_password(password: str) -> str:
    """生成 `pbkdf2_sha256$<iters>$<salt>$<hash>` 格式的口令哈希。"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验口令。格式非法或不匹配均返回 False。"""
    try:
        algo, iters, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
    except (ValueError, AttributeError):
        return False
    return secrets.compare_digest(dk.hex(), expected)


# ---- JWT -----------------------------------------------------------------

def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    """签发访问令牌，返回 (token, expiresIn 秒)。"""
    expires_in = settings.jwt_expire_seconds
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def _decode_token(token: str) -> dict:
    """解码并校验签名/过期；失败抛 PyJWTError（调用方转 401）。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def revoke_token(jti: str) -> None:
    _revoked_jti.add(jti)


# ---- 依赖 ----------------------------------------------------------------

def _current_payload(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """校验 Bearer 令牌，返回载荷。无/失效/已退出 → 401（→ 信封 code 1002）。"""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    try:
        payload = _decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    if payload.get("jti") in _revoked_jti:
        raise HTTPException(status_code=401, detail="令牌已失效（已退出登录）")
    return payload


def get_current_user(
    payload: dict = Depends(_current_payload),
    db: Session = Depends(get_db),
) -> User:
    """从令牌还原当前用户。用户不存在 → 401。"""
    user = db.get(User, payload.get("sub"))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理端依赖：非 admin → 403（→ 信封 code 1003）。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="无权限：需要管理员角色")
    return user


# ---- 用户上下文（模型管理 21.3+：per-user 当前模型） -----------------------

class UserContextMiddleware(BaseHTTPMiddleware):
    """把请求者 user_id 绑定到 contextvar（model_registry per-user overlay 解析用）。

    best-effort：仅解 JWT 取 sub（不查库、不拦截），无/无效 token → 绑 None。
    在 call_next **之前** set，下游端点/由请求派生的 asyncio 任务继承同一上下文
    ——生成接口签名零改动即可按用户分发模型（与 envelope.TraceIdMiddleware 同机制）。
    鉴权语义完全不变：get_current_user 仍是唯一的认证依赖。
    """

    async def dispatch(self, request, call_next):  # type: ignore[override]
        from app.core import model_registry  # 局部导入避免启动期循环依赖

        user_id: str | None = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                user_id = _decode_token(auth[7:]).get("sub")
            except jwt.PyJWTError:
                user_id = None
        token = model_registry.bind_user(user_id)
        try:
            return await call_next(request)
        finally:
            model_registry.unbind_user(token)
