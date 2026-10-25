"""Housekeeping that runs on a timer: expired guests, codes and sessions.

Deleting a guest's sources fires the database trigger that drops any
document nobody else holds, so storage never fills with abandoned content.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.domain.models import utcnow
from app.domain.ports import IdentityRepository

log = logging.getLogger(__name__)


class MaintenanceService:
    def __init__(
        self,
        identity: IdentityRepository,
        *,
        guest_ttl: timedelta,
        interval: timedelta = timedelta(hours=1),
    ):
        self._identity = identity
        self._guest_ttl = guest_ttl
        self._interval = interval

    async def run_once(self) -> dict[str, int]:
        now = utcnow()
        guests = await self._identity.purge_expired_guests(now - self._guest_ttl)
        codes = await self._identity.purge_login_codes(now)
        sessions = await self._identity.purge_sessions(now)
        if guests or codes or sessions:
            log.info("cleanup: %d guests, %d login codes, %d sessions removed", guests, codes, sessions)
        return {"guests": guests, "login_codes": codes, "sessions": sessions}

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let housekeeping kill the loop
                log.exception("cleanup pass failed")
            await asyncio.sleep(self._interval.total_seconds())
