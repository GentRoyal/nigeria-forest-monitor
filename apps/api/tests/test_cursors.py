from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.api.app.security.cursors import (
    CursorError,
    CursorPosition,
    cursor_scope,
    decode_cursor,
    encode_cursor,
)


def test_signed_cursor_round_trip_and_scope_binding() -> None:
    position = CursorPosition(
        created_at=datetime(2026, 8, 10, 9, 30, tzinfo=UTC),
        resource_id=uuid4(),
    )
    scope = cursor_scope({"organisation_id": "one", "action": "member."})
    encoded = encode_cursor(position, scope=scope)

    assert decode_cursor(encoded, scope=scope) == position
    with pytest.raises(CursorError):
        decode_cursor(encoded, scope=cursor_scope({"organisation_id": "two"}))


def test_signed_cursor_rejects_tampering() -> None:
    position = CursorPosition(created_at=datetime.now(UTC), resource_id=uuid4())
    scope = cursor_scope({"organisation_id": "one"})
    encoded = encode_cursor(position, scope=scope)
    index = len(encoded) // 2
    replacement = "A" if encoded[index] != "A" else "B"
    tampered = f"{encoded[:index]}{replacement}{encoded[index + 1 :]}"

    with pytest.raises(CursorError):
        decode_cursor(tampered, scope=scope)
