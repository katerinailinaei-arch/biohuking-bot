from hashlib import sha256


def content_hash(text: str) -> str:
    """Return the exact UTF-8 content hash used by review and approval records."""
    return sha256(text.encode("utf-8")).hexdigest()

