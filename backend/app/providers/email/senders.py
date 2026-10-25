"""Email delivery for login codes.

Two implementations: the console sender logs the message (development and
tests), and the Resend sender posts it to https://resend.com, whose free
tier is enough for a small launch. Adding a provider means one more class
here and one more branch in `build_email_sender`.
"""

from __future__ import annotations

import logging

import httpx

from app.core.exceptions import AppError
from app.domain.ports import EmailSender

log = logging.getLogger(__name__)


class EmailDeliveryError(AppError):
    status_code = 502
    code = "email_failed"


class ConsoleEmailSender(EmailSender):
    """Prints the email to the server log instead of sending it."""

    name = "console"

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, text: str) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})
        log.info("email to %s | %s\n%s", to, subject, text)


class ResendEmailSender(EmailSender):
    name = "resend"

    def __init__(self, http: httpx.AsyncClient, *, api_key: str, sender: str):
        self._http = http
        self._api_key = api_key
        self._from = sender

    async def send(self, *, to: str, subject: str, text: str) -> None:
        try:
            response = await self._http.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"from": self._from, "to": [to], "subject": subject, "text": text},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise EmailDeliveryError("The email service could not be reached.") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("message", "")
            except ValueError:
                pass
            log.warning("resend returned %s: %s", response.status_code, detail or response.text[:200])
            raise EmailDeliveryError(
                "The login email could not be sent. " + (detail or f"HTTP {response.status_code}.")
            )


def build_email_sender(provider: str, http: httpx.AsyncClient, *, api_key: str, sender: str) -> EmailSender:
    name = (provider or "console").strip().lower()
    if name == "console":
        return ConsoleEmailSender()
    if name == "resend":
        if not api_key:
            raise AppError("EMAIL_PROVIDER=resend needs RESEND_API_KEY.")
        return ResendEmailSender(http, api_key=api_key, sender=sender)
    raise AppError(f"Unknown EMAIL_PROVIDER {provider!r}; use console or resend.")
