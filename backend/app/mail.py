import smtplib
from email.message import EmailMessage
from urllib.parse import unquote, urlsplit

from app.config import settings


def send(to: str, subject: str, body: str) -> None:
    """Send a plain-text email through the server in SMTP_URL: implicit TLS for smtps, STARTTLS when offered."""
    if not settings.smtp_url or not settings.mail_from:
        raise RuntimeError("SMTP_URL and MAIL_FROM must both be set to send email")
    url = urlsplit(settings.smtp_url)
    if url.scheme not in ("smtp", "smtps"):
        raise ValueError(f"SMTP_URL has scheme {url.scheme!r}, not smtp or smtps")
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if url.scheme == "smtps":
        server = smtplib.SMTP_SSL(url.hostname, url.port or 465, timeout=20)
    else:
        server = smtplib.SMTP(url.hostname, url.port or 587, timeout=20)
    with server:
        if url.scheme == "smtp":
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
        if url.username:
            server.login(unquote(url.username), unquote(url.password or ""))
        server.send_message(message)
