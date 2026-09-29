"""Sending a short message to a person: email through Resend, text messages through
Twilio. Each is off until its keys are set (see the README's configuration table);
the app shows only the ways that are set up. Used for the weekly highlight."""

import logging

import httpx

from . import config

log = logging.getLogger(__name__)
TIMEOUT = 20


def email_ready() -> bool:
    return bool(config.RESEND_API_KEY and config.MAIL_FROM)


def sms_ready() -> bool:
    return bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_FROM)


async def send_email(to: str, subject: str, text: str, html: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        r = await http.post("https://api.resend.com/emails",
                            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
                            json={"from": config.MAIL_FROM, "to": [to], "subject": subject, "text": text, "html": html})
    r.raise_for_status()


async def send_sms(to: str, text: str) -> None:
    sid = config.TWILIO_ACCOUNT_SID
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        r = await http.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                            auth=(sid, config.TWILIO_AUTH_TOKEN),
                            data={"From": config.TWILIO_FROM, "To": to, "Body": text})
    r.raise_for_status()
