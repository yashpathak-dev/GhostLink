import base64
import hashlib
from cryptography.fernet import Fernet


class EphemeralCipher:
    """
    Client-side symmetric encryption using AES-128-CBC + HMAC-SHA256 (Fernet).
    Keys are derived in-memory from a shared Room Passphrase/Key and never stored on disk.
    """

    def __init__(self, room_secret: str):
        digest = hashlib.sha256(room_secret.encode("utf-8")).digest()
        self._key = base64.urlsafe_b64encode(digest)
        self._cipher = Fernet(self._key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._cipher.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._cipher.decrypt(ciphertext).decode("utf-8")
        except Exception:
            return "[UNDECRYPTABLE_PACKET: Invalid Room Key]"