"""Remove recognizable credentials and private contact details from log copies."""

import re


REDACTED = "[已脱敏]"
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
_CONNECTION = re.compile(r"(?:https?|wss?)://[^\s<>\"'？，；。、]+(?:kaggleusercontent\.com|kaggle\.net)(?=[/:?]|$)[^\s<>\"'？，；。、]*", re.I)
_SECRET = re.compile(
    r"((?:[?&#]|\b)(?:access[_-]?token|api[_-]?key|password|token|secret)\s*[\"']?\s*[:=]\s*"
    r"|\b(?:authorization\s*:\s*)?bearer\s+|(?:密码|令牌|密钥)\s*(?:是|[:=：])\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s&#\"'，。；、<>{}]+)", re.I,
)
_KNOWN_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{20,})\b")
_SECRET_FIELDS = {"authorization","password","access_token","api_key","apikey","token","secret","private_key","connection_url"}
_EMAIL = re.compile(r"\b[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ABO_IDENTITY = re.compile(r"^(?:B[0-9][A-Z0-9]{8}|[0-9]{9}[0-9X])@", re.I)


def redact_log(value):
    """Return a log-safe copy without changing the response used by the caller."""
    if isinstance(value, str):
        value = _PRIVATE_KEY.sub(REDACTED, value)
        value = _CONNECTION.sub(REDACTED, value)
        value = _SECRET.sub(lambda match: match[1] + REDACTED, value)
        value = _KNOWN_TOKEN.sub(REDACTED, value)
        return _EMAIL.sub(lambda match: match[0] if _ABO_IDENTITY.match(match[0]) else REDACTED, value)
    if isinstance(value, dict):
        return {key: REDACTED if str(key).casefold().replace("-", "_") in _SECRET_FIELDS
                else redact_log(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_log(item) for item in value]
    return value
