from uuid import uuid4

import pytest

from apps.api.app.security.passwords import hash_password, validate_password, verify_password
from apps.api.app.security.permissions import Action, Role, is_allowed
from apps.api.app.security.tokens import decode_access_token, issue_access_token


def test_passwords_are_argon2id_hashed_and_verified() -> None:
    password_hash = hash_password("Forest-Monitor-2026!")
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "Forest-Monitor-2026!")
    assert not verify_password(password_hash, "incorrect-password")
    assert "Forest-Monitor-2026!" not in password_hash


@pytest.mark.parametrize(
    "password",
    ["short", "alllowercasebutlong", "1234567890123456"],
)
def test_password_policy_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValueError):
        validate_password(password)


def test_access_token_contains_only_stable_identity_claims() -> None:
    user_id, organisation_id, session_id = uuid4(), uuid4(), uuid4()
    token, _ = issue_access_token(
        user_id=user_id,
        organisation_id=organisation_id,
        session_id=session_id,
    )
    claims = decode_access_token(token)
    assert claims["sub"] == str(user_id)
    assert claims["org"] == str(organisation_id)
    assert claims["sid"] == str(session_id)
    assert "role" not in claims
    assert "email" not in claims


def test_role_permissions_match_the_approved_access_matrix() -> None:
    assert is_allowed(Role.OWNER, Action.MANAGE_ORGANISATION)
    assert is_allowed(Role.ADMINISTRATOR, Action.CONTROL_MONITORING)
    assert not is_allowed(Role.ADMINISTRATOR, Action.REMOTE_REVIEW)
    assert is_allowed(Role.ANALYST, Action.REMOTE_REVIEW)
    assert not is_allowed(Role.ANALYST, Action.MANAGE_MEMBERS)
    assert not is_allowed(Role.VIEWER, Action.VIEW_SITE)


def test_sensitive_and_verification_access_require_explicit_context() -> None:
    assert not is_allowed(Role.ANALYST, Action.VIEW_SITE, sensitive_site=True)
    assert is_allowed(
        Role.ANALYST,
        Action.VIEW_SITE,
        sensitive_site=True,
        has_team_grant=True,
    )
    assert not is_allowed(Role.VERIFICATION_OFFICER, Action.INSTITUTIONAL_VERIFY)
    assert is_allowed(
        Role.VERIFICATION_OFFICER,
        Action.INSTITUTIONAL_VERIFY,
        has_assignment=True,
    )
    assert not is_allowed(Role.VIEWER, Action.VIEW_APPROVED_SUMMARY)
    assert is_allowed(
        Role.VIEWER,
        Action.VIEW_APPROVED_SUMMARY,
        approved_summary=True,
    )
