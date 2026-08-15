import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from ..settings import get_settings


class CursorError(ValueError):
    """Raised when an opaque cursor is malformed, modified, or out of scope."""


@dataclass(frozen=True)
class CursorPosition:
    created_at: datetime
    resource_id: UUID


def cursor_scope(values: dict[str, object]) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_cursor(position: CursorPosition, *, scope: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "created_at": position.created_at.astimezone(UTC).isoformat(),
            "id": str(position.resource_id),
            "scope": scope,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(
        get_settings().cursor_signing_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def decode_cursor(value: str, *, scope: str) -> CursorPosition:
    if not value or len(value) > 1024:
        raise CursorError("cursor has an invalid length")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
        if len(decoded) <= hashlib.sha256().digest_size:
            raise CursorError("cursor is incomplete")
        payload = decoded[: -hashlib.sha256().digest_size]
        supplied_signature = decoded[-hashlib.sha256().digest_size :]
        expected_signature = hmac.new(
            get_settings().cursor_signing_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise CursorError("cursor signature is invalid")
        document = json.loads(payload)
        if set(document) != {"v", "created_at", "id", "scope"}:
            raise CursorError("cursor payload is invalid")
        if document["v"] != 1 or document["scope"] != scope:
            raise CursorError("cursor is not valid for this query")
        created_at = datetime.fromisoformat(document["created_at"])
        if created_at.tzinfo is None:
            raise CursorError("cursor timestamp is invalid")
        resource_id = UUID(document["id"])
    except CursorError:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise CursorError("cursor payload is invalid") from error
    return CursorPosition(created_at=created_at.astimezone(UTC), resource_id=resource_id)
