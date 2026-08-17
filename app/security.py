import hmac
import hashlib


def short_fingerprint(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


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


def signature_diagnostics(raw_body: bytes, signature_header: str | None, secret: str) -> dict[str, int | bool | str]:
    expected = expected_signature(raw_body, secret) if secret else ""
    signature = signature_header or ""
    return {
        "api_key_present": bool(secret),
        "api_key_length": len(secret),
        "api_key_fingerprint": short_fingerprint(secret),
        "raw_body_length": len(raw_body),
        "signature_present": bool(signature),
        "signature_length": len(signature),
        "signature_starts_with_sha256": signature.startswith("sha256="),
        "signature_hex_lowercase": len(signature) == 71 and signature[7:].lower() == signature[7:],
        "signature_fingerprint": short_fingerprint(signature),
        "expected_signature_length": len(expected),
        "expected_signature_fingerprint": short_fingerprint(expected),
        "signature_valid": hmac.compare_digest(signature, expected),
    }
