import re


class PrivacyGuard:
    """
    Client-side & pre-storage scanner that intercepts leaked credentials,
    API keys, private tokens, or PII before packets leave or get stored.
    """

    PATTERNS = {
        "API / Bearer Token": re.compile(r"(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9\-._~+/]+=*)"),
        "Private Key Block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "Password Assignment": re.compile(r"(?i)(password|passwd|pwd|secret)\s*[:=]\s*\S+"),
        "Email Address": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
        "Indian Phone / Aadhaar Pattern": re.compile(r"\b(\+91[\-\s]?)?[6-9]\d{9}\b|\b\d{4}\s?\d{4}\s?\d{4}\b"),
    }

    @classmethod
    def inspect(cls, text: str) -> tuple[bool, str | None]:
        for label, pattern in cls.PATTERNS.items():
            if pattern.search(text):
                return False, f"Blocked by AI Privacy Guard: Potential {label} detected."
        return True, None