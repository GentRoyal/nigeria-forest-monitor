import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..settings import get_settings

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


def _peppered(password: str) -> str:
    pepper = get_settings().password_pepper.encode("utf-8")
    return hmac.new(pepper, password.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    if len(password) > 256:
        raise ValueError("password is too long")
    character_groups = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if sum(character_groups) < 3:
        raise ValueError("password must use at least three character groups")


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(_peppered(password))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, _peppered(password))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
