"""Who is asking: guests, email-code login, sessions.

Two kinds of principal. A guest is an id the browser made up and sends on
every request; its data lives until it has been quiet for a day. A user
signed in by typing a code we emailed; a session token identifies them
across devices and lasts a week, extended on use.

Codes and tokens are stored only as HMAC hashes, so a database leak does
not hand out working logins.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timedelta

from app.core.exceptions import (
    AuthRequiredError,
    NotFoundError,
    SessionExpiredError,
    ValidationError,
)
from app.core.ids import new_id
from app.domain.models import (
    LoginCode,
    Principal,
    PrincipalKind,
    Session,
    User,
    utcnow,
)
from app.domain.ports import EmailSender, IdentityRepository

log = logging.getLogger(__name__)

GUEST_ID_RE = re.compile(r"^gst_[A-Za-z0-9_-]{8,64}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE_RE = re.compile(r"^\d{6}$")

#: How often a principal's last-seen time is written back, per process.
_TOUCH_INTERVAL_SECONDS = 300.0


class AuthService:
    def __init__(
        self,
        identity: IdentityRepository,
        email: EmailSender,
        *,
        secret: str,
        guest_ttl: timedelta = timedelta(hours=24),
        session_ttl: timedelta = timedelta(days=7),
        code_ttl: timedelta = timedelta(minutes=10),
        code_max_attempts: int = 5,
        app_name: str = "LinkChat",
        reveal_codes: bool = False,
    ):
        self._identity = identity
        self._email = email
        self._secret = (secret or secrets.token_hex(32)).encode("utf-8")
        self.guest_ttl = guest_ttl
        self.session_ttl = session_ttl
        self._code_ttl = code_ttl
        self._code_max_attempts = max(1, code_max_attempts)
        self._app_name = app_name
        # Handing the code back in the API response lets anyone sign in as any
        # address, so it is only ever allowed outside production, and only when
        # the code would otherwise go nowhere but the server log.
        self._reveal_codes = reveal_codes and self._email.name == "console"
        self._touched: dict[str, float] = {}

    @property
    def email_provider(self) -> str:
        return self._email.name

    @property
    def reveals_codes(self) -> bool:
        """True only in development with the console sender, so the UI stays usable locally."""
        return self._reveal_codes

    # ---- resolving the caller ----

    async def resolve(self, *, bearer: str | None, guest_id: str | None) -> Principal:
        """Turn request credentials into a principal, or raise 401."""
        if bearer:
            return await self._resolve_session(bearer)
        if guest_id:
            return await self._resolve_guest(guest_id)
        raise AuthRequiredError("Send a session token or a guest id with the request.")

    async def _resolve_session(self, token: str) -> Principal:
        found = await self._identity.get_session(self._hash(token))
        if found is None:
            raise SessionExpiredError("This session is no longer valid. Sign in again.")
        session, user = found
        now = utcnow()
        if session.expires_at <= now:
            await self._identity.delete_session(session.id)
            raise SessionExpiredError("This session has expired. Sign in again.")
        if self._should_touch(session.id):
            session.last_seen_at = now
            session.expires_at = now + self.session_ttl
            await self._identity.touch_session(session.id, now, session.expires_at)
        return Principal(owner_id=user.id, kind=PrincipalKind.USER, user=user, session=session)

    async def _resolve_guest(self, guest_id: str) -> Principal:
        if not GUEST_ID_RE.match(guest_id):
            raise AuthRequiredError("The guest id is malformed.")
        if self._should_touch(guest_id):
            await self._identity.touch_guest(guest_id)
        return Principal(owner_id=guest_id, kind=PrincipalKind.GUEST)

    def _should_touch(self, key: str) -> bool:
        now = time.monotonic()
        last = self._touched.get(key)
        if last is not None and now - last < _TOUCH_INTERVAL_SECONDS:
            return False
        self._touched[key] = now
        if len(self._touched) > 10_000:
            cutoff = now - _TOUCH_INTERVAL_SECONDS
            self._touched = {k: v for k, v in self._touched.items() if v > cutoff}
        return True

    # ---- email codes ----

    async def request_code(self, email: str, display_name: str | None) -> tuple[LoginCode, str]:
        """Create and email a six-digit code. Returns the record and the raw code."""
        address = self._clean_email(email)
        name = self._clean_name(display_name) if display_name else None
        existing = await self._identity.get_user_by_email(address)
        if existing is None and not name:
            raise ValidationError("Tell us your name to create an account.", details={"needs_name": True})

        raw = f"{secrets.randbelow(1_000_000):06d}"
        now = utcnow()
        code = LoginCode(
            id=new_id("lgc"),
            email=address,
            display_name=name,
            code_hash=self._hash(raw),
            expires_at=now + self._code_ttl,
        )
        await self._identity.add_login_code(code)
        minutes = max(1, int(self._code_ttl.total_seconds() // 60))
        await self._email.send(
            to=address,
            subject=f"Your {self._app_name} code: {raw}",
            text=(
                f"Hi{' ' + (existing.display_name if existing else name or '')},\n\n"
                f"Your {self._app_name} sign-in code is {raw}. It works for {minutes} minutes.\n\n"
                "If you did not ask for this, you can ignore the email."
            ),
        )
        return code, raw

    async def verify_code(self, email: str, code: str, *, guest_id: str | None = None) -> tuple[str, Session, User]:
        """Check the code, create the account if needed, open a session.

        A guest id, when given, has its sources, collections and conversations
        moved into the account so nothing typed before signing in is lost.
        """
        address = self._clean_email(email)
        typed = (code or "").strip()
        if not _CODE_RE.match(typed):
            raise ValidationError("The code is six digits.")

        pending = await self._identity.latest_login_code(address)
        if pending is None:
            raise ValidationError("No code is pending for this address. Request a new one.")
        now = utcnow()
        if pending.expires_at <= now:
            raise ValidationError("That code has expired. Request a new one.")
        attempts = await self._identity.record_code_attempt(pending.id)
        if attempts > self._code_max_attempts:
            await self._identity.consume_login_code(pending.id, now)
            raise ValidationError("Too many wrong attempts. Request a new code.")
        if not hmac.compare_digest(pending.code_hash, self._hash(typed)):
            raise ValidationError(
                "That code is not right.", details={"attempts_left": self._code_max_attempts - attempts}
            )

        await self._identity.consume_login_code(pending.id, now)
        user = await self._identity.get_user_by_email(address)
        if user is None:
            user = await self._identity.add_user(
                User(
                    id=new_id("usr"),
                    email=address,
                    display_name=pending.display_name or address.split("@", 1)[0],
                    last_login_at=now,
                )
            )
            log.info("new account %s", user.id)
        else:
            await self._identity.record_login(user.id, now)

        if guest_id and GUEST_ID_RE.match(guest_id):
            await self._identity.transfer_ownership(guest_id, user.id)
            self._touched.pop(guest_id, None)

        raw_token = secrets.token_urlsafe(32)
        session = Session(
            id=new_id("ses"),
            user_id=user.id,
            token_hash=self._hash(raw_token),
            expires_at=now + self.session_ttl,
        )
        await self._identity.add_session(session)
        return raw_token, session, user

    # ---- leaving ----

    async def logout(self, principal: Principal) -> None:
        if principal.session is not None:
            await self._identity.delete_session(principal.session.id)
            self._touched.pop(principal.session.id, None)

    async def delete_account(self, principal: Principal) -> None:
        if principal.user is None:
            raise NotFoundError("Only a signed-in user has an account to delete.")
        await self._identity.delete_user(principal.user.id)

    async def forget_guest(self, principal: Principal) -> None:
        if not principal.is_guest:
            raise ValidationError("Signed-in users delete their account instead.")
        await self._identity.delete_owner(principal.owner_id)
        self._touched.pop(principal.owner_id, None)

    # ---- helpers ----

    def _hash(self, value: str) -> str:
        return hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _clean_email(email: str) -> str:
        address = (email or "").strip().lower()
        if len(address) > 254 or not _EMAIL_RE.match(address):
            raise ValidationError("That does not look like an email address.")
        return address

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join((name or "").split())
        if not cleaned:
            raise ValidationError("Tell us your name to create an account.", details={"needs_name": True})
        return cleaned[:80]

    def guest_expiry(self, now: datetime | None = None) -> datetime:
        return (now or utcnow()) + self.guest_ttl
