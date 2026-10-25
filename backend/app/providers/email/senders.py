"""Email delivery for login codes.

Three implementations: the console sender logs the message (development and
tests), the SMTP sender works with any mailbox that offers SMTP access (a
Gmail app password, Brevo's relay, Zoho), and the Resend sender posts to
https://resend.com. Adding a provider means one more class here and one more
branch in `build_email_sender`.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

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


class SmtpEmailSender(EmailSender):
    """Plain SMTP with STARTTLS (port 587) or implicit TLS (port 465).

    The standard library client is blocking, so each send runs in a worker
    thread. One connection per email is fine at login-code volumes.
    """

    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        timeout_seconds: float = 20.0,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = sender
        self._timeout = timeout_seconds

    async def send(self, *, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        try:
            await asyncio.to_thread(self._deliver, message)
        except smtplib.SMTPAuthenticationError as exc:
            log.warning("smtp login refused by %s: %s", self._host, exc)
            raise EmailDeliveryError(
                "The mail server refused the SMTP username or password. For Gmail this must be an "
                "app password, not the account password."
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("smtp delivery via %s failed: %s", self._host, exc)
            raise EmailDeliveryError("The login email could not be sent. Try again in a minute.") from exc

    def _deliver(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context) as smtp:
                smtp.login(self._username, self._password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(self._username, self._password)
            smtp.send_message(message)


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


def build_email_sender(
    provider: str,
    http: httpx.AsyncClient,
    *,
    api_key: str,
    sender: str,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_username: str = "",
    smtp_password: str = "",
) -> EmailSender:
    name = (provider or "console").strip().lower()
    if name == "console":
        return ConsoleEmailSender()
    if name == "smtp":
        required = (("SMTP_HOST", smtp_host), ("SMTP_USERNAME", smtp_username), ("SMTP_PASSWORD", smtp_password))
        missing = [key for key, value in required if not value]
        if missing:
            raise AppError("EMAIL_PROVIDER=smtp needs " + ", ".join(missing) + ".")
        return SmtpEmailSender(
            host=smtp_host, port=smtp_port, username=smtp_username, password=smtp_password, sender=sender
        )
    if name == "resend":
        if not api_key:
            raise AppError("EMAIL_PROVIDER=resend needs RESEND_API_KEY.")
        return ResendEmailSender(http, api_key=api_key, sender=sender)
    raise AppError(f"Unknown EMAIL_PROVIDER {provider!r}; use console, smtp or resend.")
