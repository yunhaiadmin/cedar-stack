"""凭据密封（全站唯一实现）：Fernet 加密，密钥由 SECRET_KEY 派生。

原属已删除的上游源模块，因被 TOTP（account_security）、
115 转存、Drive OAuth、用户绑定、挂载凭据等多条活链路共用，
2026-11-14 上游全清时迁到本中立模块——密封格式与密钥派生逐字
不变，存量密封值继续可解。

SECRET_KEY 一旦更换，旧密封值解不开——此时 unseal_secret 响亮
抛 SealedSecretError，调用方须按「凭据失效、请管理员重录」处理，
绝不静默回落明文、空凭据或旧凭据。SECRET_KEY 未配置时 security
会生成随机值：密封在本进程内可用，重启后解不开——生产必须配
固定 SECRET_KEY（play_sign 本就依赖它同步，属既有要求）。
"""

from __future__ import annotations

import base64
import hashlib

#: 密封值前缀：格式变了就换前缀，旧值解不开时响亮报错不静默兼容。
_SEAL_PREFIX = "fernet-v1:"


class SealedSecretError(RuntimeError):
    """密封凭据解不开（密钥已换或值损坏）：凭据失效，须管理员重录。"""


def _sealing_key() -> bytes:
    """由 SECRET_KEY 派生 Fernet 密钥（32 字节摘要 → urlsafe base64）。"""
    from backend.security import SECRET_KEY

    digest = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)



def seal_secret(plaintext: str) -> str:
    """密封凭据原文，返回带前缀的密封值（存库只存这个，不存原文）。"""
    from cryptography.fernet import Fernet

    token = Fernet(_sealing_key()).encrypt(plaintext.encode("utf-8"))
    return _SEAL_PREFIX + token.decode("ascii")


def unseal_secret(sealed: str) -> str:
    """解开密封值。值损坏/前缀不对/密钥已换 → SealedSecretError（响亮）。"""
    from cryptography.fernet import Fernet, InvalidToken

    if not sealed or not sealed.startswith(_SEAL_PREFIX):
        raise SealedSecretError("密封凭据格式不正确（非本模块密封值）")
    try:
        raw = Fernet(_sealing_key()).decrypt(
            sealed[len(_SEAL_PREFIX):].encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise SealedSecretError(
            "密封凭据解不开：SECRET_KEY 已更换或值已损坏，须管理员重录") from exc
    return raw.decode("utf-8")
