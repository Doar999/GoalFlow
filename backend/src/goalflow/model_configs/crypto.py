"""用户模型凭证的信封加密（T14 决策 A10）。

07 号文档"凭证与运行快照"节：密文入 model_configs，主密钥由部署环境提供
（GOALFLOW_CREDENTIAL_ENCRYPTION_KEY，T03 预置），不与密文同表；密钥版本随行记录，
为将来轮换留位。信封结构：每条凭证一个随机数据密钥（DEK），DEK 再被主密钥包裹——
单条密文被破解不波及其他行，轮换主密钥只需重包 DEK 层。

主密钥是部署者配置的任意非占位字符串（T03 的会话令牌也直接用它做 HMAC），
这里用 SHA-256 派生成 AES-256 密钥。密文 blob 布局（base64 前的原始字节）：

    TAG(4) | nonce_dek(12) | len(enc_dek)(2) | enc_dek | nonce_cred(12) | enc_credential

enc_dek = AESGCM(KEK).encrypt(nonce_dek, dek, aad=TAG)
enc_credential = AESGCM(DEK).encrypt(nonce_cred, plaintext, aad=TAG)

nonce 均为每次加密随机生成；AAD 绑定版本标签，改版本的历史密文解密即失败——
这是有意的：换版本必须走重加密迁移，不能靠静默兼容。
"""

import base64
import binascii
import hashlib
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from goalflow.contracts.errors import ErrorCode, GoalflowError

_ENVELOPE_TAG: Final = b"GFC1"
_NONCE_BYTES: Final = 12
_DEK_BYTES: Final = 32
_LENGTH_PREFIX_BYTES: Final = 2
# encryption_key_version 列的当前取值。主密钥轮换时递增（"v2"…），旧版本密文由迁移重包。
KEY_VERSION: Final = "v1"
_MIN_MASTER_KEY_LENGTH: Final = 32


class CredentialCrypto:
    """持有派生后的主密钥；服务层单例使用，测试可按不同主密钥各建一个。"""

    def __init__(self, master_key: str) -> None:
        if len(master_key) < _MIN_MASTER_KEY_LENGTH:
            raise ValueError("GOALFLOW_CREDENTIAL_ENCRYPTION_KEY 过短（至少 32 字符）")
        # 部署者的密钥是任意字符串；AES-256 需要 32 字节定长密钥，用 SHA-256 派生。
        self._kek: Final[bytes] = hashlib.sha256(master_key.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> tuple[str, str]:
        """加密一条凭证，返回 (密文 base64, 密钥版本)。"""
        dek = AESGCM.generate_key(bit_length=_DEK_BYTES * 8)
        nonce_dek, nonce_cred = os.urandom(_NONCE_BYTES), os.urandom(_NONCE_BYTES)
        enc_dek = AESGCM(self._kek).encrypt(nonce_dek, dek, associated_data=_ENVELOPE_TAG)
        enc_credential = AESGCM(dek).encrypt(nonce_cred, plaintext.encode("utf-8"), associated_data=_ENVELOPE_TAG)
        blob = (
            _ENVELOPE_TAG
            + nonce_dek
            + len(enc_dek).to_bytes(_LENGTH_PREFIX_BYTES, "big")
            + enc_dek
            + nonce_cred
            + enc_credential
        )
        return base64.b64encode(blob).decode("ascii"), KEY_VERSION

    def decrypt(self, ciphertext_b64: str) -> str:
        """解密一条凭证。密文被篡改或主密钥不匹配时抛 INTERNAL_ERROR——那不是用户能修的事。"""
        try:
            blob = base64.b64decode(ciphertext_b64.encode("ascii"), validate=True)
            header = len(_ENVELOPE_TAG) + _NONCE_BYTES + _LENGTH_PREFIX_BYTES
            if not blob.startswith(_ENVELOPE_TAG) or len(blob) < header + _NONCE_BYTES:
                raise ValueError("密文布局不符合当前信封格式")
            nonce_dek = blob[len(_ENVELOPE_TAG) : len(_ENVELOPE_TAG) + _NONCE_BYTES]
            dek_length = int.from_bytes(blob[len(_ENVELOPE_TAG) + _NONCE_BYTES : header], "big")
            enc_dek = blob[header : header + dek_length]
            nonce_cred = blob[header + dek_length : header + dek_length + _NONCE_BYTES]
            enc_credential = blob[header + dek_length + _NONCE_BYTES :]
            dek = AESGCM(self._kek).decrypt(nonce_dek, enc_dek, associated_data=_ENVELOPE_TAG)
            plaintext = AESGCM(dek).decrypt(nonce_cred, enc_credential, associated_data=_ENVELOPE_TAG)
        except (InvalidTag, ValueError, IndexError, binascii.Error) as exc:
            raise GoalflowError(
                ErrorCode.INTERNAL_ERROR,
                "凭证密文无法解密：主密钥不匹配或密文损坏",
            ) from exc
        return plaintext.decode("utf-8")
