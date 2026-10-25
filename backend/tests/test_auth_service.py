"""Sign-in rules that must hold regardless of the HTTP layer."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.exceptions import AuthRequiredError, SessionExpiredError, ValidationError
from app.domain.models import utcnow
from app.providers.email.senders import ConsoleEmailSender
from app.services.auth_service import AuthService
from tests.fakes import MemoryIdentityRepository, MemoryStore


def make(reveal: bool = True, **overrides) -> tuple[AuthService, MemoryStore, ConsoleEmailSender]:
    store = MemoryStore()
    email = ConsoleEmailSender()
    service = AuthService(
        MemoryIdentityRepository(store), email, secret="s3cret", reveal_codes=reveal, **overrides
    )
    return service, store, email


async def test_codes_are_never_revealed_in_production():
    production, _, _ = make(reveal=False)
    development, _, _ = make(reveal=True)
    assert production.reveals_codes is False
    assert development.reveals_codes is True


async def test_only_hashes_are_stored_and_the_code_arrives_by_email():
    service, store, email = make()
    code, raw = await service.request_code("Ada@Example.com", "Ada")
    assert code.email == "ada@example.com", "addresses are normalised"
    assert raw not in code.code_hash and len(raw) == 6
    assert raw in email.sent[0]["text"]
    assert store.login_codes[code.id].code_hash == code.code_hash


async def test_verify_creates_the_account_and_a_session_then_resolves_it():
    service, store, _ = make(session_ttl=timedelta(hours=1))
    _, raw = await service.request_code("ada@example.com", "Ada")
    token, session, user = await service.verify_code("ada@example.com", raw)
    assert user.display_name == "Ada" and store.users[user.id] is user
    assert token not in store.sessions, "the raw token is not stored"

    principal = await service.resolve(bearer=token, guest_id=None)
    assert principal.kind.value == "user" and principal.owner_id == user.id

    # a used code cannot be replayed
    with pytest.raises(ValidationError):
        await service.verify_code("ada@example.com", raw)

    # expiry is enforced on resolve, and the session is dropped
    session.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(SessionExpiredError):
        await service.resolve(bearer=token, guest_id=None)
    assert session.token_hash not in store.sessions


async def test_wrong_codes_lock_out_after_the_limit():
    service, _, _ = make(code_max_attempts=2)
    await service.request_code("ada@example.com", "Ada")
    for _ in range(2):
        with pytest.raises(ValidationError, match="not right"):
            await service.verify_code("ada@example.com", "000000")
    with pytest.raises(ValidationError, match="Too many"):
        await service.verify_code("ada@example.com", "000000")


async def test_guest_ids_must_look_like_guest_ids():
    service, store, _ = make()
    principal = await service.resolve(bearer=None, guest_id="gst_abcdefgh12345678")
    assert principal.is_guest and "gst_abcdefgh12345678" in store.guests
    with pytest.raises(AuthRequiredError):
        await service.resolve(bearer=None, guest_id="../etc/passwd")
    with pytest.raises(AuthRequiredError):
        await service.resolve(bearer=None, guest_id=None)
