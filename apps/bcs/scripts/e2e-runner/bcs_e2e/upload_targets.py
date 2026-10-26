"""Bounded small-file upload plan, consuming product-returned targets verbatim."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit


class UploadTargetError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadPart:
    url: str
    content: bytes
    part_number: int | None  # None is the single-upload mode, not a missing binding.


def _target_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise UploadTargetError("upload target is missing a URL")
    return value


def upload_parts(target: dict, content: bytes, expected_size: int) -> list[UploadPart]:
    if type(expected_size) is not int or len(content) != expected_size:
        raise UploadTargetError("upload bytes do not match the prepared size")
    if target.get("method") != "PUT":
        raise UploadTargetError("upload target does not declare PUT")
    if target.get("mode") == "single":
        parts = [UploadPart(_target_url(target.get("upload_url")), content, None)]
    elif target.get("mode") == "multipart":
        size, count, values = target.get("part_size"), target.get("part_count"), target.get("parts")
        if (type(size) is not int or size <= 0 or type(count) is not int or not 1 <= count <= 64
                or count != (len(content) + size - 1) // size
                or not isinstance(values, list) or len(values) != count):
            raise UploadTargetError("invalid or unbounded multipart target")
        numbers = [part.get("part_number") if isinstance(part, dict) else None for part in values]
        if (any(type(n) is not int or not 1 <= n <= 65535 for n in numbers)
                or numbers != sorted(set(numbers))):
            raise UploadTargetError("multipart part numbers must be ordered and unique")
        parts = [UploadPart(_target_url(part.get("upload_url")), content[i * size:(i + 1) * size], numbers[i])
                 for i, part in enumerate(values)]
    else:
        raise UploadTargetError("unsupported or missing upload mode")
    if len({part.url for part in parts}) != len(parts):
        raise UploadTargetError("multipart targets must be distinct")
    return parts


def proxy_target(url: str, expected_path: str, part_number: int | None) -> bool:
    parts = urlsplit(url)
    # Rust's URL serializer leaves ':' in a path segment while Python's
    # quote(safe='') encodes it. Compare exact decoded segments, not whole paths;
    # encoded separators/traversal must never gain product credentials.
    def segments(path):
        values = [unquote(segment, errors="strict") for segment in path.split("/")]
        if any(value in {".", ".."} or any(c in value for c in "/\\%") for value in values):
            return None
        return values
    try:
        actual, expected = segments(parts.path), segments(expected_path)
    except UnicodeError:
        return False
    if actual is None or expected is None or actual != expected:
        return False
    expected_query = [] if part_number is None else [("part", str(part_number))]
    return parse_qsl(parts.query, keep_blank_values=True) == expected_query
