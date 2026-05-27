"""认证辅助函数

当前阶段只提供最小可用的密码哈希与校验，不引入额外第三方依赖。
"""

from __future__ import annotations

import hashlib
import hmac


def hash_password(password: str, pepper: str) -> str:
    """对密码做稳定哈希

    说明：
        - 这轮先用 sha256(password + pepper) 做最小可用版
        - 后续如果要增强强度，可平滑迁到 bcrypt/argon2
    """
    raw = f"{pepper}:{password}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_password(password: str, pepper: str, password_hash: str) -> bool:
    """校验密码明文与已存哈希是否匹配"""
    expected = hash_password(password, pepper)
    return hmac.compare_digest(expected, password_hash)
