import hmac
import hashlib


def expected_signature(raw_body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False
    expected = expected_signature(raw_body, secret)
    return hmac.compare_digest(signature_header, expected)


def signature_diagnostics(raw_body: bytes, signature_header: str | None, secret: str) -> dict[str, int | bool]:
    expected = expected_signature(raw_body, secret) if secret else ""
    return {
        "api_key_present": bool(secret),
        "api_key_length": len(secret),
        "signature_present": bool(signature_header),
        "signature_length": len(signature_header or ""),
        "expected_signature_length": len(expected),
        "signature_valid": hmac.compare_digest(signature_header or "", expected),
    }
