"""Email helpers for ShadeMatch.

This module centralizes how the app builds and sends transactional emails so
that every message we put in users' inboxes is:

* Visually polished — multipart/alternative HTML + plain-text rendered from
  Jinja templates under ``templates/emails/``.
* Friendly to mainstream spam filters (Gmail, Outlook, Apple Mail, Yahoo) —
  proper ``From`` display name, ``Reply-To``, ``Date``, ``Message-ID``,
  ``MIME-Version``, ``List-Unsubscribe`` (with one-click POST per RFC 8058 for
  bulk reminders), ``Auto-Submitted``/``X-Auto-Response-Suppress`` for
  transactional messages, and a unique ``X-Entity-Ref-ID`` to keep Gmail from
  collapsing them.
* Compliant with Gmail/Yahoo bulk sender requirements introduced in 2024 (real
  unsubscribe link + one-click POST endpoint for non-transactional mail).

Note: SPF, DKIM, and DMARC must still be configured on the sending domain at
the DNS / mail-provider layer for deliverability — that part of the puzzle
lives outside this codebase.
"""

from __future__ import annotations

import os
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Mapping, Optional, Tuple

from dotenv import dotenv_values
from flask import current_app, render_template
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


# ── Branding / defaults ────────────────────────────────────────────────────

BRAND_NAME = 'ShadeMatch'
DEFAULT_FROM_DISPLAY = 'ShadeMatch'
DEFAULT_BASE_URL = 'https://shadematch.app'

# itsdangerous salt + lifetime for one-click unsubscribe links. We don't expire
# them aggressively so people can act on an old reminder without trouble.
UNSUBSCRIBE_SALT = 'shadematch-email-unsubscribe-v1'
UNSUBSCRIBE_TOKEN_TTL_SEC = 60 * 60 * 24 * 365 * 2  # 2 years


# ── SMTP settings ──────────────────────────────────────────────────────────

def resolve_email_settings() -> dict:
    """Build the SMTP config from env vars (with a fallback to a sibling .env).

    Kept compatible with the legacy resolver previously inlined in routes.py.
    """
    settings = {
        'host': os.environ.get('SMTP_HOST', '').strip(),
        'port': int(os.environ.get('SMTP_PORT', '587') or '587'),
        'user': os.environ.get('SMTP_USER', '').strip(),
        'password': (
            os.environ.get('SMTP_PASSWORD', '').strip()
            or os.environ.get('SMTP_PASS', '').strip()
        ),
        'sender': (
            os.environ.get('SMTP_SENDER_EMAIL', '').strip()
            or os.environ.get('SMTP_FROM', '').strip()
        ),
        'use_tls': (os.environ.get('SMTP_USE_TLS', 'true').strip().lower() != 'false'),
        'use_ssl': (os.environ.get('SMTP_USE_SSL', 'false').strip().lower() == 'true'),
    }
    if all([settings['host'], settings['user'], settings['password'], settings['sender']]):
        return settings

    candidate_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'maxillofacialisrehabilitacio', '.env')),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'maxillofacialisrehabilitacio', '.env')),
    ]
    for candidate in candidate_paths:
        if not os.path.exists(candidate):
            continue
        values = dotenv_values(candidate)
        settings['host'] = settings['host'] or (values.get('SMTP_HOST') or '').strip()
        settings['port'] = settings['port'] if settings['port'] else int(values.get('SMTP_PORT') or 587)
        settings['user'] = settings['user'] or (values.get('SMTP_USER') or '').strip()
        settings['password'] = settings['password'] or (
            (values.get('SMTP_PASSWORD') or '').strip()
            or (values.get('SMTP_PASS') or '').strip()
        )
        settings['sender'] = settings['sender'] or (
            (values.get('SMTP_SENDER_EMAIL') or '').strip()
            or (values.get('SMTP_FROM') or '').strip()
            or (values.get('SMTP_USER') or '').strip()
        )
        if values.get('SMTP_USE_TLS') is not None:
            settings['use_tls'] = str(values.get('SMTP_USE_TLS')).strip().lower() != 'false'
        if values.get('SMTP_USE_SSL') is not None:
            settings['use_ssl'] = str(values.get('SMTP_USE_SSL')).strip().lower() == 'true'
        break
    return settings


# ── URL / branding helpers ────────────────────────────────────────────────

def base_url(request_url_root: Optional[str] = None) -> str:
    """Return an absolute base URL for links inside emails.

    Priority: APP_BASE_URL env > current request URL root > hard-coded default.
    """
    explicit = (os.environ.get('APP_BASE_URL') or '').strip()
    if explicit:
        return explicit.rstrip('/')
    if request_url_root:
        return request_url_root.rstrip('/')
    return DEFAULT_BASE_URL


def from_display_name() -> str:
    return os.environ.get('EMAIL_FROM_NAME', DEFAULT_FROM_DISPLAY) or DEFAULT_FROM_DISPLAY


def footer_address() -> Optional[str]:
    """Optional postal address for CAN-SPAM friendliness; empty by default."""
    val = (os.environ.get('EMAIL_FOOTER_ADDRESS') or '').strip()
    return val or None


# ── Unsubscribe token ──────────────────────────────────────────────────────

def _serializer() -> URLSafeTimedSerializer:
    secret = current_app.config.get('SECRET_KEY') or 'dev'
    return URLSafeTimedSerializer(secret, salt=UNSUBSCRIBE_SALT)


def make_unsubscribe_token(user_id: str) -> str:
    return _serializer().dumps(user_id)


def verify_unsubscribe_token(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=UNSUBSCRIBE_TOKEN_TTL_SEC)
    except (BadSignature, SignatureExpired):
        return None


def build_unsubscribe_url(user_id: str, request_url_root: Optional[str] = None) -> str:
    token = make_unsubscribe_token(user_id)
    return f"{base_url(request_url_root)}/email/unsubscribe?token={token}"


# ── Rendering ──────────────────────────────────────────────────────────────

def render_email(template_name: str, **context) -> Tuple[str, str]:
    """Render the HTML and plain-text variants of an email template.

    Templates live at ``templates/emails/<template_name>.html`` and
    ``templates/emails/<template_name>.txt``.
    """
    ctx = {
        'brand_name': BRAND_NAME,
        'footer_address': footer_address(),
        **context,
    }
    html = render_template(f'emails/{template_name}.html', **ctx)
    text = render_template(f'emails/{template_name}.txt', **ctx)
    return html, text


# ── Sending ────────────────────────────────────────────────────────────────

def send_email(
    to_email: str,
    subject: str,
    *,
    html: str,
    text: str,
    list_unsubscribe_url: Optional[str] = None,
    one_click_unsubscribe: bool = False,
    transactional: bool = True,
    extra_headers: Optional[Mapping[str, str]] = None,
) -> None:
    """Send a multipart/alternative email with deliverability-friendly headers.

    Parameters
    ----------
    list_unsubscribe_url:
        If provided, a ``List-Unsubscribe`` header (RFC 2369) is emitted with
        both a https URL and a ``mailto:`` fallback, plus ``Precedence: bulk``.
    one_click_unsubscribe:
        If True, also emit ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``
        (RFC 8058) — required for Gmail/Yahoo bulk senders since 2024.
    transactional:
        If True (default), include ``Auto-Submitted: auto-generated`` and
        ``X-Auto-Response-Suppress`` so vacation responders stay quiet.
    """
    settings = resolve_email_settings()
    if not all([settings['host'], settings['user'], settings['password'], settings['sender']]):
        raise RuntimeError('Email sender is not configured')

    sender_email = settings['sender']
    sender_domain = sender_email.split('@')[-1] if '@' in sender_email else 'shadematch.app'

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((from_display_name(), sender_email))
    msg['To'] = to_email
    msg['Reply-To'] = sender_email
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid('shadematch', domain=sender_domain)
    msg['MIME-Version'] = '1.0'
    msg['X-Entity-Ref-ID'] = uuid.uuid4().hex

    msg.set_content(text)
    msg.add_alternative(html, subtype='html')

    if list_unsubscribe_url:
        msg['List-Unsubscribe'] = (
            f'<{list_unsubscribe_url}>, <mailto:{sender_email}?subject=unsubscribe>'
        )
        if one_click_unsubscribe:
            msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        msg['Precedence'] = 'bulk'

    if transactional:
        msg['Auto-Submitted'] = 'auto-generated'
        msg['X-Auto-Response-Suppress'] = 'OOF, AutoReply'

    for header_name, header_value in (extra_headers or {}).items():
        msg[header_name] = header_value

    smtp_cls = smtplib.SMTP_SSL if settings['use_ssl'] else smtplib.SMTP
    with smtp_cls(settings['host'], settings['port'], timeout=15) as server:
        if settings['use_tls'] and not settings['use_ssl']:
            server.starttls()
        server.login(settings['user'], settings['password'])
        server.send_message(msg)


# ── Convenience: high-level builders for our standard emails ──────────────

def send_verification_email(*, to_email: str, verify_url: str, ttl_hours: int, username: Optional[str] = None) -> None:
    html, text = render_email(
        'verify',
        verify_url=verify_url,
        ttl_hours=ttl_hours,
        username=username,
    )
    send_email(
        to_email=to_email,
        subject='Confirm your email and start mixing colors',
        html=html,
        text=text,
        transactional=True,
    )


def send_recovery_email(*, to_email: str, recovery_url: str, ttl_minutes: int) -> None:
    html, text = render_email(
        'recover_id',
        recovery_url=recovery_url,
        ttl_minutes=ttl_minutes,
    )
    send_email(
        to_email=to_email,
        subject='Recover your ShadeMatch player ID',
        html=html,
        text=text,
        transactional=True,
    )


def send_daily_reminder_email(
    *,
    to_email: str,
    user_id: str,
    context: dict,
    request_url_root: Optional[str] = None,
) -> None:
    """Send a polished daily challenge reminder.

    ``context`` should contain at minimum: ``headline``, ``cta_url``. Optional
    fields: ``eyebrow``, ``subhead``, ``swatch_hex``, ``swatch_label``,
    ``swatch_caption``, ``stats`` (list of {label, value}), ``streak``,
    ``completed``, ``total``, ``tip``.
    """
    unsubscribe_url = build_unsubscribe_url(user_id, request_url_root=request_url_root)
    ctx = {
        'username': user_id,
        'list_unsubscribe_url': unsubscribe_url,
        **context,
    }
    html, text = render_email('daily_reminder', **ctx)
    send_email(
        to_email=to_email,
        subject=context.get('subject') or "Today's ShadeMatch challenge is live",
        html=html,
        text=text,
        list_unsubscribe_url=unsubscribe_url,
        one_click_unsubscribe=True,
        transactional=False,
    )
