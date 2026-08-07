import logging
import smtplib
from email.mime.text import MIMEText

from ..config import SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME

log = logging.getLogger(__name__)


def send_mail(recipients: list[str], subject: str, body: str) -> bool:
    if not SMTP_HOST or not recipients:
        log.warning("SMTP niet geconfigureerd of geen ontvangers — mail niet verstuurd")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(recipients)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as server:
            server.starttls()
            if SMTP_USERNAME:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, recipients, msg.as_string())
        return True
    except Exception:
        log.exception("Mail versturen mislukt")
        return False
