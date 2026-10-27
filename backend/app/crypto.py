from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    """用应用密钥加密保存的敏感设置（115 凭据、TMDB Key）。

    和 TokenSigner 一样按用途派生独立子密钥。应用密钥更换后旧密文无法解密，
    decrypt 返回 None，调用方按「未设置」处理，由管理员重新填写。
    """

    def __init__(self, secret_key: str, purpose: str) -> None:
        key = hmac.new(secret_key.encode(), purpose.encode(), hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str | None:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except (InvalidToken, ValueError):
            return None
