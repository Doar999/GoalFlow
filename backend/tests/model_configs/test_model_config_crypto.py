"""信封加密测试：往返、唯一性、防篡改与主密钥绑定（T14 决策 A10）。"""

import pytest

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.model_configs.crypto import KEY_VERSION, CredentialCrypto

_MASTER = "unit-test-master-key-not-for-production-0123456789abcdef"


class TestEnvelopeRoundTrip:
    def test_roundtrip_preserves_plaintext(self) -> None:
        crypto = CredentialCrypto(_MASTER)
        ciphertext, key_version = crypto.encrypt("sk-ant-demo-1234567890")
        assert key_version == KEY_VERSION == "v1"
        assert crypto.decrypt(ciphertext) == "sk-ant-demo-1234567890"

    def test_same_plaintext_encrypts_differently(self) -> None:
        # 随机 DEK 与 nonce：同明文两次加密的密文必须不同，否则密文本身泄露等值信息。
        crypto = CredentialCrypto(_MASTER)
        first, _ = crypto.encrypt("same-credential")
        second, _ = crypto.encrypt("same-credential")
        assert first != second

    def test_ciphertext_carries_no_plaintext(self) -> None:
        import base64

        crypto = CredentialCrypto(_MASTER)
        ciphertext, _ = crypto.encrypt("sk-plain-visible-secret")
        assert "plain-visible" not in ciphertext
        assert b"plain-visible" not in base64.b64decode(ciphertext)


class TestTamperAndKeyBinding:
    def test_tampered_ciphertext_rejected(self) -> None:
        import base64

        crypto = CredentialCrypto(_MASTER)
        ciphertext, _ = crypto.encrypt("sk-ant-demo-1234567890")
        blob = bytearray(base64.b64decode(ciphertext))
        blob[-1] ^= 0x01
        tampered = base64.b64encode(bytes(blob)).decode("ascii")
        with pytest.raises(GoalflowError) as excinfo:
            crypto.decrypt(tampered)
        assert excinfo.value.code == ErrorCode.INTERNAL_ERROR

    def test_wrong_master_key_rejected(self) -> None:
        crypto = CredentialCrypto(_MASTER)
        ciphertext, _ = crypto.encrypt("sk-ant-demo-1234567890")
        other = CredentialCrypto(_MASTER[:-1] + "X")
        with pytest.raises(GoalflowError) as excinfo:
            other.decrypt(ciphertext)
        assert excinfo.value.code == ErrorCode.INTERNAL_ERROR

    def test_short_master_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            CredentialCrypto("too-short")


class TestPerformance:
    def test_bulk_encrypt_decrypt_is_fast_enough(self) -> None:
        # 加密在请求路径上执行：100 条加解密必须在百毫秒级完成，防止误用慢原语。
        import time

        crypto = CredentialCrypto(_MASTER)
        started = time.monotonic()
        for index in range(100):
            ciphertext, _ = crypto.encrypt(f"sk-test-{index}")
            assert crypto.decrypt(ciphertext) == f"sk-test-{index}"
        assert time.monotonic() - started < 5.0
