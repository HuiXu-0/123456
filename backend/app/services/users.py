"""账号体系服务（B9，接口文档第 16 章）。

覆盖：
- 16.1 注册：用户名查重 + 密码强度校验（≥6）+ 初始化未诊断旅程 + 空掌握度；
- 16.2 管理端用户列表：分页 + 已通过知识点数 + 最近活跃时间；
- 16.3 重置学习进度：清空 Mastery 与 Journey（幂等，禁对 admin）；
- 16.4 启用/禁用：翻转 isActive（禁对 admin，防自锁）。

校验失败以 UserServiceError 抛出（带业务码 + HTTP status），由 API 层转统一信封。
后端为账号与进度的权威数据源。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.entities import Journey, Mastery, User
from app.services.mastery import STATUS_PASSED

_MIN_PASSWORD_LEN = 6


class UserServiceError(Exception):
    """账号服务领域错误：携带业务码（1.3）与 HTTP status，供 API 层套信封。"""

    def __init__(self, code: int, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def create_learner(db: Session, username: str, password: str) -> User:
    """注册新学习者（接口文档 16.1）。

    - 用户名查重（已存在 → 1001）；
    - 密码强度 ≥6（过弱 → 1001）；
    - role 固定 learner、isActive=true；初始化未诊断 Journey、空 Mastery。
    """
    username = username.strip()
    if not username:
        raise UserServiceError(1001, "用户名不能为空", 400)
    if len(password) < _MIN_PASSWORD_LEN:
        raise UserServiceError(1001, "密码长度至少为 6 位", 400)
    if db.query(User).filter(User.username == username).first() is not None:
        raise UserServiceError(1001, "用户名已存在", 400)

    user = User(
        id="u_" + uuid.uuid4().hex[:12],
        username=username,
        display_name=username,
        password_hash=hash_password(password),
        role="learner",
        is_active=True,
    )
    db.add(user)
    # 初始旅程：未诊断、未生成路径（Mastery 为空 = 全部未开始）
    db.add(Journey(user_id=user.id, has_diagnosed=False, has_generated_path=False))
    db.commit()
    return user


def change_password(db: Session, user: User, old_password: str, new_password: str) -> User:
    """修改当前用户口令（接口文档 3.4）。

    - 旧密码不符 → 1001（与登录侧 verify_password 同一哈希校验逻辑，复用既有用户表）；
    - 新密码强度 ≥6（过弱 → 1001，与注册 16.1 同口径）；
    - 通过则以同一 hash_password 写回 password_hash（pbkdf2_sha256，无外部依赖/Key）。
    """
    if not verify_password(old_password, user.password_hash):
        raise UserServiceError(1001, "旧密码错误", 400)
    if len(new_password) < _MIN_PASSWORD_LEN:
        raise UserServiceError(1001, "密码长度至少为 6 位", 400)
    user.password_hash = hash_password(new_password)
    db.commit()
    return user


def touch_last_active(db: Session, user: User) -> None:
    """登录成功刷新最近活跃时间（接口文档 16.0/16.2）。"""
    user.last_active_at = datetime.now(timezone.utc)
    db.commit()


def list_users(db: Session, page: int, page_size: int) -> dict:
    """管理端用户列表（接口文档 16.2）。按 createdAt 升序，分页稳定。"""
    base = db.query(User).order_by(User.created_at.asc(), User.id.asc())
    total = base.count()
    rows = base.offset((page - 1) * page_size).limit(page_size).all()

    # 一次性统计各用户 passed 知识点数（避免 N+1）
    passed_counts: dict[str, int] = {}
    for uid, in db.query(Mastery.user_id).filter(Mastery.status == STATUS_PASSED).all():
        passed_counts[uid] = passed_counts.get(uid, 0) + 1
    row_ids = [u.id for u in rows]
    diagnosed = (
        {
            j.user_id: bool(j.has_diagnosed)
            for j in db.query(Journey).filter(Journey.user_id.in_(row_ids)).all()
        }
        if row_ids
        else {}
    )

    items = [
        {
            "userId": u.id,
            "username": u.username,
            "role": u.role,
            "isActive": bool(u.is_active),
            "hasDiagnosed": diagnosed.get(u.id, False),
            "passedKpCount": passed_counts.get(u.id, 0),
            "createdAt": _iso(u.created_at),
            "lastActiveAt": _iso(u.last_active_at),
        }
        for u in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def _require_non_admin_target(db: Session, user_id: str) -> User:
    """取目标用户；不存在 → 1004，目标为 admin → 1003（禁止对管理员操作）。"""
    user = db.get(User, user_id)
    if user is None:
        raise UserServiceError(1004, "用户不存在", 404)
    if user.role == "admin":
        raise UserServiceError(1003, "不允许对管理员账号执行该操作", 403)
    return user


def reset_progress(db: Session, user_id: str) -> dict:
    """重置学习进度（接口文档 16.3，幂等）：清空 Mastery + 复位 Journey。"""
    user = _require_non_admin_target(db, user_id)
    cleared = db.query(Mastery).filter(Mastery.user_id == user.id).delete()
    journey = db.get(Journey, user.id)
    if journey is None:
        db.add(Journey(user_id=user.id, has_diagnosed=False, has_generated_path=False))
    else:
        journey.has_diagnosed = False
        journey.has_generated_path = False
        journey.target_job_name = None
        journey.match_pct = None
    db.commit()
    return {"userId": user.id, "hasDiagnosed": False, "masteryCleared": int(cleared)}


def toggle_active(db: Session, user_id: str) -> dict:
    """启用/禁用账号（接口文档 16.4）：翻转 isActive；禁对 admin（防自锁）。"""
    user = _require_non_admin_target(db, user_id)
    user.is_active = not bool(user.is_active)
    db.commit()
    return {"userId": user.id, "isActive": bool(user.is_active)}
