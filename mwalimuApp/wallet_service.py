"""Wallet domain logic: atomic credit/debit, top-up, withdraw, escrow.

All balance mutations must go through this module so the SELECT FOR UPDATE
locking + idempotency invariants hold.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from mwalimuApp.models import (
    Booking,
    Escrow,
    SasaPayTransaction,
    UserAccount,
    Wallet,
    WalletTransaction,
)
from mwalimuApp.services import SasaPayService

TWO = Decimal("0.01")


# ---------------------------------------------------------------------------
# Wallet lookup
# ---------------------------------------------------------------------------

def get_user_wallet(user: UserAccount) -> Wallet:
    owner_type = (
        Wallet.OwnerType.TEACHER
        if user.user_type == UserAccount.UserTypes.TEACHER
        else Wallet.OwnerType.SCHOOL
    )
    wallet, _ = Wallet.objects.get_or_create(
        owner=user,
        defaults={"owner_type": owner_type},
    )
    return wallet


def get_platform_wallet() -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(
        owner=None,
        owner_type=Wallet.OwnerType.PLATFORM,
    )
    return wallet


def _new_ref(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:18]}"


# ---------------------------------------------------------------------------
# Atomic primitives (must run inside transaction.atomic with row locked)
# ---------------------------------------------------------------------------

def _credit(wallet: Wallet, amount: Decimal, *, tx_type: str, reference: str,
            description: str = "", booking: Optional[Booking] = None,
            status: str = WalletTransaction.Status.SUCCESS,
            metadata: Optional[dict] = None) -> WalletTransaction:
    wallet.available_balance = (Decimal(wallet.available_balance) + Decimal(amount)).quantize(TWO)
    wallet.save(update_fields=["available_balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet, tx_type=tx_type, direction=WalletTransaction.Direction.CREDIT,
        amount=Decimal(amount).quantize(TWO), balance_after=wallet.available_balance,
        status=status, reference=reference, description=description,
        related_booking=booking, metadata=metadata or {},
    )


def _debit(wallet: Wallet, amount: Decimal, *, tx_type: str, reference: str,
           description: str = "", booking: Optional[Booking] = None,
           status: str = WalletTransaction.Status.SUCCESS,
           metadata: Optional[dict] = None) -> WalletTransaction:
    if Decimal(wallet.available_balance) < Decimal(amount):
        raise ValueError("Insufficient funds")
    wallet.available_balance = (Decimal(wallet.available_balance) - Decimal(amount)).quantize(TWO)
    wallet.save(update_fields=["available_balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet, tx_type=tx_type, direction=WalletTransaction.Direction.DEBIT,
        amount=Decimal(amount).quantize(TWO), balance_after=wallet.available_balance,
        status=status, reference=reference, description=description,
        related_booking=booking, metadata=metadata or {},
    )


def _lock(wallet_id: int) -> Wallet:
    return Wallet.objects.select_for_update().get(pk=wallet_id)


# ---------------------------------------------------------------------------
# C2B — Top-up
# ---------------------------------------------------------------------------

def initiate_topup(user: UserAccount, amount: Decimal, phone: str,
                   network_code: str = "63902") -> dict:
    """Trigger STK push and create a PENDING wallet tx. Wallet is credited
    only when the SasaPay C2B callback confirms success."""
    amount = Decimal(amount).quantize(TWO)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")

    wallet = get_user_wallet(user)
    ref = _new_ref("topup")

    # Pre-create the PENDING tx so the callback has something to update.
    wallet_tx = WalletTransaction.objects.create(
        wallet=wallet,
        tx_type=WalletTransaction.TxType.TOPUP,
        direction=WalletTransaction.Direction.CREDIT,
        amount=amount,
        balance_after=wallet.available_balance,
        status=WalletTransaction.Status.PENDING,
        reference=ref,
        description=f"Wallet top-up via STK push",
        metadata={"phone": phone, "network_code": network_code},
    )

    svc = SasaPayService()
    try:
        resp = svc.request_payment(
            phone=phone,
            amount=str(amount),
            account_reference=ref,
            description=f"Mwalimu Pool top-up · {user.email}",
            network_code=network_code,
        )
    except Exception as exc:
        wallet_tx.status = WalletTransaction.Status.FAILED
        wallet_tx.metadata = {**wallet_tx.metadata, "error": str(exc)}
        wallet_tx.save(update_fields=["status", "metadata", "updated_at"])
        raise

    sp = SasaPayTransaction.objects.create(
        kind=SasaPayTransaction.Kind.C2B,
        merchant_request_id=str(resp.get("MerchantRequestID") or resp.get("merchantRequestID") or ""),
        checkout_request_id=str(resp.get("CheckoutRequestID") or resp.get("checkoutRequestID") or ""),
        merchant_reference=ref,
        phone=phone,
        amount=amount,
        raw_request=resp,
        wallet_tx=wallet_tx,
    )

    return {
        "reference": ref,
        "checkout_request_id": sp.checkout_request_id,
        "merchant_request_id": sp.merchant_request_id,
        "provider_response": resp,
        "wallet_tx_id": wallet_tx.id,
    }


def handle_c2b_callback(payload: dict) -> Optional[SasaPayTransaction]:
    """Idempotently complete a C2B top-up from a SasaPay callback body."""
    checkout_id = (
        payload.get("CheckoutRequestID")
        or payload.get("checkoutRequestID")
        or payload.get("checkout_request_id")
    )
    merchant_ref = (
        payload.get("MerchantRequestID")
        or payload.get("merchantRequestID")
        or payload.get("AccountReference")
        or payload.get("account_reference")
    )
    result_code = payload.get("ResultCode", payload.get("resultCode"))
    receipt = (
        payload.get("TransactionReceipt")
        or payload.get("MpesaReceiptNumber")
        or payload.get("transactionReceipt")
        or ""
    )

    sp = (
        SasaPayTransaction.objects.filter(checkout_request_id=str(checkout_id)).first()
        if checkout_id else None
    )
    if not sp and merchant_ref:
        sp = SasaPayTransaction.objects.filter(merchant_reference=str(merchant_ref)).first()
    if not sp:
        return None

    if sp.status == SasaPayTransaction.Status.SUCCESS:
        return sp  # idempotent

    sp.raw_callback = payload
    sp.provider_reference = str(receipt)

    success = str(result_code) in ("0", "00", "200") or payload.get("status") == "SUCCESS"

    if not sp.wallet_tx:
        sp.status = SasaPayTransaction.Status.SUCCESS if success else SasaPayTransaction.Status.FAILED
        sp.save()
        return sp

    with transaction.atomic():
        wallet_tx = WalletTransaction.objects.select_for_update().get(pk=sp.wallet_tx_id)
        if wallet_tx.status == WalletTransaction.Status.SUCCESS:
            sp.status = SasaPayTransaction.Status.SUCCESS
            sp.save()
            return sp

        if success:
            wallet = _lock(wallet_tx.wallet_id)
            wallet.available_balance = (
                Decimal(wallet.available_balance) + Decimal(wallet_tx.amount)
            ).quantize(TWO)
            wallet.save(update_fields=["available_balance", "updated_at"])
            wallet_tx.status = WalletTransaction.Status.SUCCESS
            wallet_tx.balance_after = wallet.available_balance
            wallet_tx.save(update_fields=["status", "balance_after", "updated_at"])
            sp.status = SasaPayTransaction.Status.SUCCESS
        else:
            wallet_tx.status = WalletTransaction.Status.FAILED
            wallet_tx.save(update_fields=["status", "updated_at"])
            sp.status = SasaPayTransaction.Status.FAILED

        sp.save()

    return sp


# ---------------------------------------------------------------------------
# B2C — Withdrawal
# ---------------------------------------------------------------------------

def initiate_withdrawal(user: UserAccount, amount: Decimal, phone: str,
                        channel: str = "63902") -> dict:
    """Reserve funds, then call B2C. Callback reconciles success/failure."""
    amount = Decimal(amount).quantize(TWO)
    min_amt = Decimal(str(getattr(settings, "MIN_WITHDRAWAL_KES", 100)))
    if amount < min_amt:
        raise ValueError(f"Minimum withdrawal is KES {min_amt}")

    ref = _new_ref("wd")

    with transaction.atomic():
        wallet = _lock(get_user_wallet(user).pk)
        if Decimal(wallet.available_balance) < amount:
            raise ValueError("Insufficient available balance")
        wallet_tx = _debit(
            wallet, amount,
            tx_type=WalletTransaction.TxType.WITHDRAWAL,
            reference=ref,
            description=f"Withdrawal to {phone}",
            status=WalletTransaction.Status.PENDING,
            metadata={"phone": phone, "channel": channel},
        )

    svc = SasaPayService()
    try:
        resp = svc.send_b2c(
            receiver_number=phone,
            amount=str(amount),
            merchant_transaction_reference=ref,
            reason="Mwalimu Pool wallet withdrawal",
            channel=channel,
        )
    except Exception as exc:
        # Refund the reserved amount immediately.
        with transaction.atomic():
            wallet = _lock(wallet.pk)
            _credit(
                wallet, amount,
                tx_type=WalletTransaction.TxType.ADJUSTMENT,
                reference=_new_ref("wd-refund"),
                description=f"Auto-refund for failed withdrawal {ref}",
                metadata={"failed_ref": ref, "error": str(exc)},
            )
            wallet_tx.status = WalletTransaction.Status.FAILED
            wallet_tx.metadata = {**wallet_tx.metadata, "error": str(exc)}
            wallet_tx.save(update_fields=["status", "metadata", "updated_at"])
        raise

    sp = SasaPayTransaction.objects.create(
        kind=SasaPayTransaction.Kind.B2C,
        merchant_reference=ref,
        merchant_request_id=str(resp.get("MerchantRequestID", "")),
        checkout_request_id=str(resp.get("CheckoutRequestID", "")),
        phone=phone,
        amount=amount,
        raw_request=resp,
        wallet_tx=wallet_tx,
    )

    return {
        "reference": ref,
        "merchant_request_id": sp.merchant_request_id,
        "provider_response": resp,
        "wallet_tx_id": wallet_tx.id,
    }


def handle_b2c_callback(payload: dict) -> Optional[SasaPayTransaction]:
    """On failure, refund the reserved amount to the user's wallet."""
    merchant_ref = (
        payload.get("MerchantTransactionReference")
        or payload.get("merchantTransactionReference")
        or payload.get("OriginatorConversationID")
    )
    result_code = payload.get("ResultCode", payload.get("resultCode"))
    receipt = (
        payload.get("TransactionID")
        or payload.get("TransactionReceipt")
        or payload.get("transactionId")
        or ""
    )

    sp = SasaPayTransaction.objects.filter(merchant_reference=str(merchant_ref)).first()
    if not sp:
        return None
    if sp.status in (SasaPayTransaction.Status.SUCCESS, SasaPayTransaction.Status.FAILED):
        return sp

    sp.raw_callback = payload
    sp.provider_reference = str(receipt)
    success = str(result_code) in ("0", "00", "200") or payload.get("status") == "SUCCESS"

    if not sp.wallet_tx:
        sp.status = SasaPayTransaction.Status.SUCCESS if success else SasaPayTransaction.Status.FAILED
        sp.save()
        return sp

    with transaction.atomic():
        wallet_tx = WalletTransaction.objects.select_for_update().get(pk=sp.wallet_tx_id)
        if wallet_tx.status == WalletTransaction.Status.SUCCESS:
            sp.status = SasaPayTransaction.Status.SUCCESS
            sp.save()
            return sp

        if success:
            wallet_tx.status = WalletTransaction.Status.SUCCESS
            wallet_tx.save(update_fields=["status", "updated_at"])
            sp.status = SasaPayTransaction.Status.SUCCESS
        else:
            wallet = _lock(wallet_tx.wallet_id)
            _credit(
                wallet, wallet_tx.amount,
                tx_type=WalletTransaction.TxType.ADJUSTMENT,
                reference=_new_ref("wd-refund"),
                description=f"Refund for failed withdrawal {wallet_tx.reference}",
                metadata={"failed_ref": wallet_tx.reference},
            )
            wallet_tx.status = WalletTransaction.Status.FAILED
            wallet_tx.save(update_fields=["status", "updated_at"])
            sp.status = SasaPayTransaction.Status.FAILED
        sp.save()

    return sp


# ---------------------------------------------------------------------------
# Escrow
# ---------------------------------------------------------------------------

def hold_escrow(booking: Booking) -> Escrow:
    """Debit school wallet, create Escrow row, mark booking 'held'."""
    amount = Decimal(booking.gross_amount).quantize(TWO)
    fee_pct = Decimal(str(getattr(settings, "PLATFORM_FEE_PERCENT", 10)))
    fee = (amount * fee_pct / Decimal("100")).quantize(TWO)
    ref = _new_ref(f"hold-{booking.pk}")

    with transaction.atomic():
        if Escrow.objects.filter(booking=booking).exists():
            return booking.escrow
        school_wallet = _lock(get_user_wallet(booking.school).pk)
        teacher_wallet = get_user_wallet(booking.teacher)
        _debit(
            school_wallet, amount,
            tx_type=WalletTransaction.TxType.BOOKING_HOLD,
            reference=ref,
            description=f"Booking #{booking.pk} escrow",
            booking=booking,
        )
        escrow = Escrow.objects.create(
            booking=booking,
            school_wallet=school_wallet,
            teacher_wallet=teacher_wallet,
            amount=amount,
            fee_amount=fee,
        )
        booking.payment_status = "held"
        booking.save(update_fields=["payment_status", "updated_at"])
    return escrow


def release_escrow(booking: Booking) -> Escrow:
    """Split held funds: fee → Platform, remainder → Teacher."""
    with transaction.atomic():
        escrow = Escrow.objects.select_for_update().get(booking=booking)
        if escrow.status != Escrow.Status.HELD:
            return escrow

        platform = _lock(get_platform_wallet().pk)
        teacher = _lock(escrow.teacher_wallet_id)

        fee = Decimal(escrow.fee_amount)
        payout = (Decimal(escrow.amount) - fee).quantize(TWO)

        if fee > 0:
            _credit(
                platform, fee,
                tx_type=WalletTransaction.TxType.FEE,
                reference=_new_ref(f"fee-{booking.pk}"),
                description=f"Platform fee · Booking #{booking.pk}",
                booking=booking,
            )
        _credit(
            teacher, payout,
            tx_type=WalletTransaction.TxType.BOOKING_RELEASE,
            reference=_new_ref(f"rel-{booking.pk}"),
            description=f"Earnings · Booking #{booking.pk}",
            booking=booking,
        )
        escrow.status = Escrow.Status.RELEASED
        escrow.released_at = timezone.now()
        escrow.save(update_fields=["status", "released_at"])

        booking.payment_status = "paid"
        booking.save(update_fields=["payment_status", "updated_at"])
    return escrow


def refund_escrow(booking: Booking, reason: str = "") -> Escrow:
    """Return held escrow to the school wallet (admin / dispute resolution)."""
    with transaction.atomic():
        escrow = Escrow.objects.select_for_update().get(booking=booking)
        if escrow.status != Escrow.Status.HELD:
            return escrow
        school = _lock(escrow.school_wallet_id)
        _credit(
            school, escrow.amount,
            tx_type=WalletTransaction.TxType.BOOKING_REFUND,
            reference=_new_ref(f"ref-{booking.pk}"),
            description=f"Escrow refund · Booking #{booking.pk}" + (f" · {reason}" if reason else ""),
            booking=booking,
        )
        escrow.status = Escrow.Status.REFUNDED
        escrow.released_at = timezone.now()
        escrow.save(update_fields=["status", "released_at"])
        booking.payment_status = "refunded"
        booking.save(update_fields=["payment_status", "updated_at"])
    return escrow