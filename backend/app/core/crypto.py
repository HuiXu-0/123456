"""模型配置 api_key 对称加密与脱敏（模型管理 21.3+，产品化安全打底）。

三条安全红线（CC-model-management-page.md）在本模块集中兜住：
1. **加密存储**：api_key 经 Fernet（AES128-CBC + HMAC，cryptography 库）加密后才落
   SQLite，DB 中无明文。密钥材料优先取 ``settings.model_key_secret``（生产经 .env 配
   专用随机串）；缺省为空时从 ``jwt_secret`` 派生（SHA-256 → urlsafe base64），保证
   零配置也能跑（demo 兜底）且重启后可解（密钥稳定）。
2. **脱敏回显**：key 返回前端一律 ``****`` + 后四位（mask_key），完整明文只在
   服务端调用上游一瞬间存在于内存。
3. **日志红线**：redact() 把任意文本（如上游 SDK 异常串）中的完整 key 替换为脱敏形，
   供测试连通/调用失败等出错路径在打日志/回消息前统一清洗。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    """进程内单例 Fernet。密钥 = model_key_secret（优先）或 jwt_secret 派生。"""
    global _fernet_instance
    if _fernet_instance is None:
        material = settings.model_key_secret or f"zhixue-model-key:{settings.jwt_secret}"
        key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
        _fernet_instance = Fernet(key)
    return _fernet_instance


def reset() -> None:
    """重建 Fernet（密钥配置变更/测试隔离用）。"""
    global _fernet_instance
    _fernet_instance = None


def encrypt_key(plain: str) -> str:
    """加密 api_key → 可落库的 Fernet token 字符串（非明文）。"""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_key(token: str) -> str:
    """解密落库 token → 明文 key。密钥变更/数据损坏 → 返回空串（调用侧按未配 Key 降级）。"""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return ""


def mask_key(plain_or_last4: str) -> str:
    """脱敏显示：仅回显后四位（如 ****abcd）。不足 4 位整体打码。"""
    tail = plain_or_last4[-4:] if len(plain_or_last4) >= 4 else ""
    return f"****{tail}"


def redact(text: str, secret: str) -> str:
    """把文本中出现的完整 key 替换为脱敏形（日志/错误信息红线：key 不出现在任何输出）。"""
    if not secret:
        return text
    return text.replace(secret, mask_key(secret))
