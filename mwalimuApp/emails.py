"""
Email helpers for Mwalimu Pool.

All outbound emails go through here so templates and copy stay consistent.
Uses django.core.mail with the configured SMTP backend.
"""
from __future__ import annotations

import logging
import token
import uuid

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level send
# ---------------------------------------------------------------------------

def _send(subject: str, to_email: str, text_body: str, html_body: str) -> None:
    """Send an email. Failures are logged but never raised — registration must
    not fail just because SMTP is down."""
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[to_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send email to %s: %s", to_email, exc)


# ---------------------------------------------------------------------------
# Activation link helpers
# ---------------------------------------------------------------------------

def build_activation_url(user) -> str:
    """Build a frontend activation URL the user can click in their email."""
    frontend = getattr(settings, "FRONTEND_URL", "https://mwalimupool.co.ke").rstrip("/")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{frontend}/activate/{uid}/{token}"


# ---------------------------------------------------------------------------
# Shared HTML shell — keeps the warm/earthy Mwalimu Pool vibe
# ---------------------------------------------------------------------------

def _wrap(title: str, body_html: str, cta_html: str = "") -> str:
    return f"""
    <html><body style="margin:0;padding:0;background:#f7f1e8;font-family:Georgia,serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f7f1e8;padding:32px 0;">
        <tr><td align="center">
          <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(60,40,20,0.06);">
            <tr><td style="padding:32px 40px;border-bottom:1px solid #ece3d3;">
              <h1 style="margin:0;font-family:Georgia,serif;font-size:22px;color:#3a2a1a;letter-spacing:0.02em;">Mwalimu Pool</h1>
            </td></tr>
            <tr><td style="padding:36px 40px;color:#3a2a1a;font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.65;">
              <h2 style="font-family:Georgia,serif;font-weight:500;font-size:24px;color:#3a2a1a;margin:0 0 18px;">{title}</h2>
              {body_html}
              {cta_html}
            </td></tr>
            <tr><td style="padding:24px 40px;background:#faf5ec;color:#8a7860;font-size:12px;font-family:Helvetica,Arial,sans-serif;text-align:center;">
              You are receiving this email because you have an account with Mwalimu Pool.<br/>
              &copy; Mwalimu Pool · Nairobi, Kenya
            </td></tr>
          </table>
        </td></tr>
      </table>
    </body></html>
    """


def _button(label: str, url: str) -> str:
    return f"""
    <p style="margin:28px 0;">
      <a href="{url}" style="display:inline-block;background:#a0522d;color:#ffffff;text-decoration:none;
         padding:12px 28px;border-radius:999px;font-family:Helvetica,Arial,sans-serif;font-size:14px;
         font-weight:500;letter-spacing:0.02em;">{label}</a>
    </p>
    <p style="font-size:12px;color:#8a7860;word-break:break-all;">
      Or copy this link: <br/>{url}
    </p>
    """


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def send_activation_email(user) -> None:
    """For patients & admins: contains an activation link."""
    url = build_activation_url(user)
    name = user.name.strip() if user.name else "there"
    text = (
        f"Welcome to Mwalimu Pool, {name}.\n\n"
        f"Please activate your account by visiting:\n{url}\n\n"
        "If you didn't create this account you can ignore this email."
    )
    html = _wrap(
        title=f"Welcome, {name}",
        body_html=(
            "<p>Thanks for joining Mwalimu Pool. Confirm your email address "
            "to activate your account and start exploring trusted mental "
            "health professionals.</p>"
        ),
        cta_html=_button("Activate my account", url),
    )
    _send("Activate your Mwalimu Pool account", user.email, text, html)


def send_teacher_kyc_pending_email(user) -> None:
    """For Teachers after signup: no activation link, just status."""
    name = user.name.strip() if user.name else "there"
    text = (
        f"Hello {name},\n\n"
        "Thanks for applying to practise on Mwalimu Pool. Please complete your KYC "
        "submission (license, academic certificate and ID). Our team will "
        "review your credentials within 2–3 business days. You'll receive an "
        "email as soon as a decision is made.\n\n— The Mwalimu Pool team"
    )
    html = _wrap(
        title=f"Welcome, {name}",
        body_html=(
            "<p>Thanks for applying to practise on Mwalimu Pool. Please complete your "
            "KYC submission with your license, academic certificate and ID.</p>"
            "<p>Our team will review your credentials within "
            "<strong>2–3 business days</strong>. You'll receive an email as "
            "soon as a decision is made — no action needed in the meantime.</p>"
        ),
    )
    _send("Your Mwalimu Pool application is pending review", user.email, text, html)


def send_kyc_approved_email(user) -> None:
    """KYC approved → account is now active. Tell them they can sign in."""
    frontend = getattr(settings, "FRONTEND_URL", "http://localhost:8080").rstrip("/")
    login_url = f"{frontend}/login"
    name = user.name.strip() if user.name else "there"
    text = (
        f"Great news, {name}!\n\n"
        "Your Mwalimu Pool credentials have been approved. Your account is now active "
        f"and you can sign in here: {login_url}\n\n— The Mwalimu Pool team"
    )
    html = _wrap(
        title="You're approved 🎉",
        body_html=(
            f"<p>Great news, {name}. Your credentials have been verified and "
            "your teacher account is now <strong>active</strong>.</p>"
            "<p>You can sign in and start receiving patients on Mwalimu Pool.</p>"
        ),
        cta_html=_button("Sign in to Mwalimu Pool", login_url),
    )
    _send("Your Mwalimu Pool application has been approved", user.email, text, html)


def send_kyc_rejected_email(user, notes: str = "") -> None:
    """KYC rejected → include reviewer notes."""
    name = user.name.strip() if user.name else "there"
    notes_text = notes.strip() or "No additional notes were provided."
    text = (
        f"Hello {name},\n\n"
        "Unfortunately your Mwalimu Pool KYC submission was not approved at this time.\n\n"
        f"Reviewer notes:\n{notes_text}\n\n"
        "You can update your documents and resubmit at any time."
    )
    notes_html = (
        f'<div style="background:#faf0e6;border-left:3px solid #a0522d;'
        f'padding:14px 18px;margin:18px 0;border-radius:6px;color:#5a3a20;'
        f'font-style:italic;">{notes_text}</div>'
    )
    html = _wrap(
        title="Your application needs another look",
        body_html=(
            f"<p>Hello {name}, unfortunately your KYC submission was not "
            "approved at this time.</p>"
            "<p><strong>Reviewer notes:</strong></p>"
            f"{notes_html}"
            "<p>You can update your documents and resubmit at any time by "
            "signing in and revisiting your KYC.</p>"
        ),
    )
    _send("Update needed on your Mwalimu Pool application", user.email, text, html)


def send_kyc_submitted_admin_email(user, admin_email: str) -> None:
    """Notify admin that a professional's KYC is paid and ready for review."""
    name = user.name.strip() if user.name else "Unknown"
    frontend = getattr(settings, "FRONTEND_URL", "http://localhost:8080").rstrip("/")
    review_url = f"{frontend}/admin/kyc"
    text = (
        f"A new KYC submission is ready for review.\n\n"
        f"Professional: {name} ({user.email})\n"
        f"Phone: {user.phone}\n\n"
        f"Review it here: {review_url}\n\n— Mwalimu Pool System"
    )
    html = _wrap(
        title="New KYC Submission Ready for Review",
        body_html=(
            f"<p>A professional has completed payment and submitted their KYC for review.</p>"
            f"<table style='margin:18px 0;font-size:14px;'>"
            f"<tr><td style='padding:4px 12px 4px 0;color:#8a7860;'>Name</td><td><strong>{name}</strong></td></tr>"
            f"<tr><td style='padding:4px 12px 4px 0;color:#8a7860;'>Email</td><td>{user.email}</td></tr>"
            f"<tr><td style='padding:4px 12px 4px 0;color:#8a7860;'>Phone</td><td>{user.phone}</td></tr>"
            f"</table>"
        ),
        cta_html=_button("Review KYC Submission", review_url),
    )
    _send("New KYC submission awaiting review", admin_email, text, html)


def send_account_suspended_email(user, notes: str = "") -> None:
    """Notify a professional that their account has been suspended."""
    name = user.name.strip() if user.name else "there"
    notes_text = notes.strip() or "No additional notes were provided."
    text = (
        f"Hello {name},\n\n"
        "Your Mwalimu Pool account has been suspended.\n\n"
        f"Notes from the moderation team:\n{notes_text}\n\n"
        "If you believe this is an error, please contact support.\n\n— The Mwalimu Pool team"
    )
    notes_html = (
        f'<div style="background:#faf0e6;border-left:3px solid #a0522d;'
        f'padding:14px 18px;margin:18px 0;border-radius:6px;color:#5a3a20;'
        f'font-style:italic;">{notes_text}</div>'
    )
    html = _wrap(
        title="Your account has been suspended",
        body_html=(
            f"<p>Hello {name}, your Mwalimu Pool account has been <strong>suspended</strong>.</p>"
            "<p><strong>Notes from the moderation team:</strong></p>"
            f"{notes_html}"
            "<p>If you believe this is an error, please contact our support team.</p>"
        ),
    )
    _send("Your Mwalimu Pool account has been suspended", user.email, text, html)


def send_account_banned_email(user, notes: str = "") -> None:
    """Notify a professional that their account has been banned."""
    name = user.name.strip() if user.name else "there"
    notes_text = notes.strip() or "No additional notes were provided."
    text = (
        f"Hello {name},\n\n"
        "Your Mwalimu Pool account has been permanently banned.\n\n"
        f"Notes from the moderation team:\n{notes_text}\n\n"
        "If you believe this is an error, please contact support.\n\n— The Mwalimu Pool team"
    )
    notes_html = (
        f'<div style="background:#faf0e6;border-left:3px solid #a0522d;'
        f'padding:14px 18px;margin:18px 0;border-radius:6px;color:#5a3a20;'
        f'font-style:italic;">{notes_text}</div>'
    )
    html = _wrap(
        title="Your account has been banned",
        body_html=(
            f"<p>Hello {name}, your Mwalimu Pool account has been <strong>permanently banned</strong> "
            "from Mwalimu Pool.</p>"
            "<p><strong>Notes from the moderation team:</strong></p>"
            f"{notes_html}"
            "<p>If you believe this is an error, please contact our support team.</p>"
        ),
    )
    _send("Your Mwalimu Pool account has been banned", user.email, text, html)


def send_account_unsuspended_email(user) -> None:
    """Notify a professional that their account suspension has been lifted."""
    name = user.name.strip() if user.name else "there"
    frontend = getattr(settings, "FRONTEND_URL", "http://localhost:8080").rstrip("/")
    login_url = f"{frontend}/login"
    text = (
        f"Hello {name},\n\n"
        "Good news — your Mwalimu Pool account suspension has been lifted and your "
        f"account is now active again. You can sign in here: {login_url}\n\n"
        "— The Mwalimu Pool team"
    )
    html = _wrap(
        title="Your account has been reinstated",
        body_html=(
            f"<p>Hello {name}, your Mwalimu Pool account suspension has been "
            "<strong>lifted</strong>.</p>"
            "<p>Your account is now active and you can sign in and continue "
            "using Mwalimu Pool as normal.</p>"
        ),
        cta_html=_button("Sign in to Mwalimu Pool", login_url),
    )
    _send("Your Mwalimu Pool account has been reinstated", user.email, text, html)


def send_account_admitted_email(user) -> None:
    """Notify a professional that an admin has manually activated their account."""
    name = user.name.strip() if user.name else "there"
    frontend = getattr(settings, "FRONTEND_URL", "http://localhost:8080").rstrip("/")
    login_url = f"{frontend}/login"
    text = (
        f"Great news, {name}!\n\n"
        "An admin has approved and activated your Mwalimu Pool professional account. "
        f"You can now sign in and start receiving patients: {login_url}\n\n"
        "— The Mwalimu Pool team"
    )
    html = _wrap(
        title="Your account is now active 🎉",
        body_html=(
            f"<p>Great news, {name}. An admin has reviewed and "
            "<strong>activated</strong> your Mwalimu Pool professional account.</p>"
            "<p>You can now sign in and start receiving patients on Mwalimu Pool.</p>"
        ),
        cta_html=_button("Sign in to Mwalimu Pool", login_url),
    )
    _send("Your Mwalimu Pool professional account is active", user.email, text, html)

    """Send a password reset link to the user."""
    frontend = getattr(settings, "FRONTEND_URL", "http://localhost:8080").rstrip("/")
    reset_url = f"{frontend}/reset-password/{uuid}/{token}/"
    name = user.name.strip() if user.name else "there"
    text = (
        f"Hi {name},\n\n"
        "We received a request to reset your Mwalimu Pool password.\n\n"
        f"Click the link below to choose a new password (expires in 24 hours):\n{reset_url}\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password won't change.\n\n— The Mwalimu Pool team"
    )
    html = _wrap(
        title="Reset your password",
        body_html=(
            f"<p>Hi {name},</p>"
            "<p>We received a request to reset your Mwalimu Pool password. "
            "Click the button below to choose a new one. "
            "This link expires in <strong>24 hours</strong>.</p>"
            "<p>If you didn't request this, you can safely ignore this email — "
            "your password won't change.</p>"
        ),
        cta_html=_button("Reset password", reset_url),
    )
    _send("Reset your Mwalimu Pool password", user.email, text, html)