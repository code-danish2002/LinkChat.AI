"""Email senders: configuration checks and SMTP delivery without a real server."""

from __future__ import annotations

import smtplib

import httpx
import pytest

from app.core.exceptions import AppError
from app.providers.email.senders import (
    ConsoleEmailSender,
    EmailDeliveryError,
    SmtpEmailSender,
    build_email_sender,
)


def test_builder_picks_the_provider_and_demands_its_settings():
    http = httpx.AsyncClient()
    assert isinstance(build_email_sender("console", http, api_key="", sender="x"), ConsoleEmailSender)
    smtp = build_email_sender(
        "smtp", http, api_key="", sender="x", smtp_host="smtp.example.com", smtp_username="u", smtp_password="p"
    )
    assert isinstance(smtp, SmtpEmailSender) and smtp.name == "smtp"
    with pytest.raises(AppError, match="SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD"):
        build_email_sender("smtp", http, api_key="", sender="x")
    with pytest.raises(AppError, match="RESEND_API_KEY"):
        build_email_sender("resend", http, api_key="", sender="x")
    with pytest.raises(AppError, match="Unknown"):
        build_email_sender("pigeon", http, api_key="", sender="x")


class FakeSmtp:
    """Records what the sender does with the standard library client."""

    instances: list[FakeSmtp] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.steps, self.message = host, port, [], None
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.steps.append("ehlo")

    def starttls(self, context=None):
        self.steps.append("starttls")

    def login(self, username, password):
        self.steps.append(f"login:{username}")
        if password == "wrong":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, message):
        self.message = message
        self.steps.append("send")


async def test_smtp_sender_uses_starttls_on_587_and_explains_bad_logins(monkeypatch):
    FakeSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    sender = SmtpEmailSender(
        host="smtp.gmail.com", port=587, username="me@gmail.com", password="app-pass", sender="LinkChat <me@gmail.com>"
    )
    await sender.send(to="ada@example.com", subject="Your code: 123456", text="123456")
    smtp = FakeSmtp.instances[-1]
    assert smtp.steps == ["ehlo", "starttls", "ehlo", "login:me@gmail.com", "send"]
    assert smtp.message["To"] == "ada@example.com" and "123456" in smtp.message.get_content()

    bad = SmtpEmailSender(host="smtp.gmail.com", port=587, username="me@gmail.com", password="wrong", sender="x")
    with pytest.raises(EmailDeliveryError, match="app password"):
        await bad.send(to="ada@example.com", subject="s", text="t")


async def test_smtp_sender_uses_implicit_tls_on_465(monkeypatch):
    FakeSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtp)
    sender = SmtpEmailSender(host="smtp.zoho.com", port=465, username="u", password="p", sender="x")
    await sender.send(to="ada@example.com", subject="s", text="t")
    assert FakeSmtp.instances[-1].steps == ["login:u", "send"]
