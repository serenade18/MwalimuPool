"""Wallet HTTP endpoints."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from mwalimuApp.models import (
    Booking,
    Escrow,
    SasaPayTransaction,
    UserAccount,
    Wallet,
    WalletTransaction,
)
from mwalimuApp.permissions import IsAdminRole, IsSchoolRole, IsTeacherRole
from mwalimuApp.serializers import (
    EscrowSerializer,
    SasaPayTransactionSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from mwalimuApp.services import SasaPayService
from mwalimuApp.wallet_service import (
    get_platform_wallet,
    get_user_wallet,
    handle_b2c_callback,
    handle_c2b_callback,
    hold_escrow,
    initiate_topup,
    initiate_withdrawal,
    refund_escrow,
    release_escrow,
)


def _parse_amount(raw) -> Decimal:
    try:
        amt = Decimal(str(raw))
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid amount")
    if amt <= 0:
        raise ValueError("Amount must be greater than zero")
    return amt


class WalletViewSet(viewsets.ViewSet):
    """
    GET    /wallet/me/                  Current user's wallet + last txs
    GET    /wallet/transactions/        Paginated history
    POST   /wallet/topup/               C2B STK push
    GET    /wallet/topup/<ref>/         Poll status
    POST   /wallet/withdraw/            B2C withdrawal (teacher only)
    POST   /wallet/book/                Hold escrow on a booking (school)
    POST   /wallet/release/<booking>/   Release escrow (admin or auto)
    POST   /wallet/refund/<booking>/    Refund escrow (admin)
    GET    /wallet/admin/overview/      Admin dashboard data
    """

    def get_permissions(self):
        if self.action in ("withdraw",):
            return [IsTeacherRole()]
        if self.action in ("topup", "topup_status", "book"):
            return [IsAuthenticated()]
        if self.action in ("release", "refund", "admin_overview", "admin_reconciliation"):
            return [IsAdminRole()]
        return [IsAuthenticated()]

    # GET /wallet/me/
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        wallet = get_user_wallet(request.user)
        recent = wallet.transactions.all()[:10]
        return Response({
            "error": False,
            "wallet": WalletSerializer(wallet).data,
            "recent_transactions": WalletTransactionSerializer(recent, many=True).data,
        })

    # GET /wallet/transactions/
    @action(detail=False, methods=["get"], url_path="transactions")
    def transactions(self, request):
        wallet = get_user_wallet(request.user)
        qs = wallet.transactions.all()
        tx_type = request.query_params.get("tx_type")
        if tx_type:
            qs = qs.filter(tx_type=tx_type)
        limit = int(request.query_params.get("limit", 50))
        return Response({
            "error": False,
            "data": WalletTransactionSerializer(qs[:limit], many=True).data,
        })

    # POST /wallet/topup/
    @action(detail=False, methods=["post"], url_path="topup")
    def topup(self, request):
        try:
            amount = _parse_amount(request.data.get("amount"))
            phone = str(request.data.get("phone", "")).strip()
            if not phone:
                return Response({"error": True, "message": "phone is required"},
                                status=status.HTTP_400_BAD_REQUEST)
            network = str(request.data.get("network_code") or "63902")
            result = initiate_topup(request.user, amount, phone, network_code=network)
            return Response({
                "error": False,
                "message": "STK push sent. Approve the prompt on your phone.",
                **result,
            })
        except ValueError as e:
            return Response({"error": True, "message": str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({"error": True, "message": f"Top-up failed: {e}"},
                            status=status.HTTP_502_BAD_GATEWAY)

    # GET /wallet/topup/<reference>/
    @action(detail=False, methods=["get"], url_path=r"topup/(?P<reference>[^/]+)")
    def topup_status(self, request, reference=None):
        sp = SasaPayTransaction.objects.filter(
            merchant_reference=reference, kind=SasaPayTransaction.Kind.C2B,
        ).first()
        if not sp:
            return Response({"error": True, "message": "Not found"},
                            status=status.HTTP_404_NOT_FOUND)
        # Actively reconcile if the async callback hasn't arrived yet.
        if sp.status == SasaPayTransaction.Status.PENDING:
            try:
                resp = SasaPayService().transaction_status(
                    checkout_request_id=sp.checkout_request_id or None,
                    merchant_reference=sp.merchant_reference,
                )
                # Normalise SasaPay's status payload into the callback shape
                # so handle_c2b_callback can finalise the wallet credit.
                rc = resp.get("ResultCode", resp.get("resultCode"))
                status_str = (resp.get("TransactionStatus")
                              or resp.get("status")
                              or "").upper()
                if rc is not None or status_str in ("SUCCESS", "COMPLETED", "FAILED", "CANCELLED"):
                    handle_c2b_callback({
                        "CheckoutRequestID": sp.checkout_request_id,
                        "MerchantRequestID": sp.merchant_request_id,
                        "AccountReference": sp.merchant_reference,
                        "ResultCode": rc if rc is not None else (
                            "0" if status_str in ("SUCCESS", "COMPLETED") else "1"
                        ),
                        "TransactionReceipt": resp.get("TransactionReceipt")
                            or resp.get("TransactionID") or "",
                        "status": status_str or None,
                        "_source": "poll",
                    })
                    sp.refresh_from_db()
            except Exception:  # noqa: BLE001
                # Polling is best-effort; never fail the status request.
                pass
        return Response({
            "error": False,
            "data": SasaPayTransactionSerializer(sp).data,
            "wallet_tx": WalletTransactionSerializer(sp.wallet_tx).data if sp.wallet_tx else None,
        })

    # POST /wallet/withdraw/
    @action(detail=False, methods=["post"], url_path="withdraw")
    def withdraw(self, request):
        try:
            amount = _parse_amount(request.data.get("amount"))
            phone = str(request.data.get("phone", "")).strip()
            if not phone:
                return Response({"error": True, "message": "phone is required"},
                                status=status.HTTP_400_BAD_REQUEST)
            channel = str(request.data.get("channel") or "63902")
            result = initiate_withdrawal(request.user, amount, phone, channel=channel)
            return Response({
                "error": False,
                "message": "Withdrawal initiated.",
                **result,
            })
        except ValueError as e:
            return Response({"error": True, "message": str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({"error": True, "message": f"Withdrawal failed: {e}"},
                            status=status.HTTP_502_BAD_GATEWAY)

    # POST /wallet/book/   {booking_id}
    @action(detail=False, methods=["post"], url_path="book")
    def book(self, request):
        try:
            booking = Booking.objects.get(pk=request.data.get("booking_id"))
        except (Booking.DoesNotExist, ValueError, TypeError):
            return Response({"error": True, "message": "Booking not found"},
                            status=status.HTTP_404_NOT_FOUND)
        if booking.school_id != request.user.id and request.user.user_type != "admin":
            return Response({"error": True, "message": "Forbidden"},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            escrow = hold_escrow(booking)
        except ValueError as e:
            return Response({"error": True, "message": str(e), "code": "insufficient_funds"},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "error": False,
            "message": "Funds held in escrow",
            "escrow": EscrowSerializer(escrow).data,
        })

    # POST /wallet/release/<booking_id>/
    @action(detail=False, methods=["post"], url_path=r"release/(?P<booking_id>[0-9]+)")
    def release(self, request, booking_id=None):
        try:
            booking = Booking.objects.get(pk=booking_id)
        except Booking.DoesNotExist:
            return Response({"error": True, "message": "Booking not found"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            escrow = release_escrow(booking)
        except Escrow.DoesNotExist:
            return Response({"error": True, "message": "No escrow for booking"},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": False, "escrow": EscrowSerializer(escrow).data})

    # POST /wallet/refund/<booking_id>/
    @action(detail=False, methods=["post"], url_path=r"refund/(?P<booking_id>[0-9]+)")
    def refund(self, request, booking_id=None):
        try:
            booking = Booking.objects.get(pk=booking_id)
        except Booking.DoesNotExist:
            return Response({"error": True, "message": "Booking not found"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            escrow = refund_escrow(booking, reason=request.data.get("reason", ""))
        except Escrow.DoesNotExist:
            return Response({"error": True, "message": "No escrow for booking"},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": False, "escrow": EscrowSerializer(escrow).data})

    # GET /wallet/admin/overview/
    @action(detail=False, methods=["get"], url_path="admin/overview")
    def admin_overview(self, request):
        platform = get_platform_wallet()
        totals = WalletTransaction.objects.aggregate(
            fees=Sum("amount", filter=Q(tx_type="fee", status="success")),
        )
        held = Escrow.objects.filter(status=Escrow.Status.HELD).aggregate(s=Sum("amount"))["s"] or 0
        pending_withdrawals = WalletTransaction.objects.filter(
            tx_type=WalletTransaction.TxType.WITHDRAWAL,
            status=WalletTransaction.Status.PENDING,
        )
        return Response({
            "error": False,
            "platform_wallet": WalletSerializer(platform).data,
            "total_fees_earned": str(totals.get("fees") or 0),
            "total_held_in_escrow": str(held),
            "pending_withdrawals": WalletTransactionSerializer(pending_withdrawals, many=True).data,
        })

    # GET /wallet/admin/reconciliation/
    @action(detail=False, methods=["get"], url_path="admin/reconciliation")
    def admin_reconciliation(self, request):
        """Unified reconciliation view: callbacks, wallet txs, escrows.

        Filters (all optional):
          date_from, date_to (YYYY-MM-DD or ISO datetime)
          teacher_id  -> filters wallet txs on teacher's wallet + escrows.teacher_wallet
          booking_id  -> filters wallet txs.related_booking + escrows.booking
          status      -> matches sasapay/wallet/escrow status
          kind        -> c2b | b2c (sasapay only)
          limit       -> per-section cap (default 100)
        """
        q = request.query_params
        date_from = q.get("date_from")
        date_to = q.get("date_to")
        teacher_id = q.get("teacher_id")
        booking_id = q.get("booking_id")
        status_f = q.get("status")
        kind = q.get("kind")
        try:
            limit = max(1, min(int(q.get("limit", 100)), 500))
        except (TypeError, ValueError):
            limit = 100

        def _to_dt(v):
            if not v:
                return None
            return parse_datetime(v) or parse_date(v)

        df = _to_dt(date_from)
        dt = _to_dt(date_to)

        # SasaPay callbacks
        sp_qs = SasaPayTransaction.objects.all()
        if df:
            sp_qs = sp_qs.filter(created_at__gte=df)
        if dt:
            sp_qs = sp_qs.filter(created_at__lte=dt)
        if status_f:
            sp_qs = sp_qs.filter(status=status_f)
        if kind:
            sp_qs = sp_qs.filter(kind=kind)
        if booking_id:
            sp_qs = sp_qs.filter(wallet_tx__related_booking_id=booking_id)
        if teacher_id:
            sp_qs = sp_qs.filter(wallet_tx__wallet__owner_id=teacher_id)

        # Wallet transactions
        wt_qs = WalletTransaction.objects.select_related("wallet").all()
        if df:
            wt_qs = wt_qs.filter(created_at__gte=df)
        if dt:
            wt_qs = wt_qs.filter(created_at__lte=dt)
        if status_f:
            wt_qs = wt_qs.filter(status=status_f)
        if booking_id:
            wt_qs = wt_qs.filter(related_booking_id=booking_id)
        if teacher_id:
            wt_qs = wt_qs.filter(wallet__owner_id=teacher_id)

        # Escrows
        es_qs = Escrow.objects.select_related("booking", "teacher_wallet", "school_wallet").all()
        if df:
            es_qs = es_qs.filter(held_at__gte=df)
        if dt:
            es_qs = es_qs.filter(held_at__lte=dt)
        if status_f:
            es_qs = es_qs.filter(status=status_f)
        if booking_id:
            es_qs = es_qs.filter(booking_id=booking_id)
        if teacher_id:
            es_qs = es_qs.filter(teacher_wallet__owner_id=teacher_id)

        reserved_total = es_qs.filter(status=Escrow.Status.HELD).aggregate(
            s=Sum("amount"))["s"] or 0

        escrow_rows = []
        for e in es_qs.order_by("-held_at")[:limit]:
            escrow_rows.append({
                "id": e.id,
                "booking_id": e.booking_id,
                "amount": str(e.amount),
                "fee_amount": str(e.fee_amount),
                "status": e.status,
                "held_at": e.held_at,
                "released_at": e.released_at,
                "teacher_id": e.teacher_wallet.owner_id,
                "school_id": e.school_wallet.owner_id,
            })

        wt_rows = []
        for w in wt_qs.order_by("-created_at")[:limit]:
            wt_rows.append({
                **WalletTransactionSerializer(w).data,
                "wallet_owner_id": w.wallet.owner_id,
                "wallet_owner_type": w.wallet.owner_type,
            })

        return Response({
            "error": False,
            "filters": {
                "date_from": date_from, "date_to": date_to,
                "teacher_id": teacher_id, "booking_id": booking_id,
                "status": status_f, "kind": kind, "limit": limit,
            },
            "totals": {
                "reserved_in_escrow": str(reserved_total),
                "sasapay_count": sp_qs.count(),
                "wallet_tx_count": wt_qs.count(),
                "escrow_count": es_qs.count(),
            },
            "sasapay": SasaPayTransactionSerializer(
                sp_qs.order_by("-created_at")[:limit], many=True).data,
            "wallet_transactions": wt_rows,
            "escrows": escrow_rows,
        })


# ---------------------------------------------------------------------------
# Public SasaPay callbacks (no auth)
# ---------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([AllowAny])
def sasapay_c2b_callback(request):
    sp = handle_c2b_callback(request.data if isinstance(request.data, dict) else {})
    return Response({"ResultCode": 0, "ResultDesc": "Accepted",
                     "matched": bool(sp), "status": sp.status if sp else None})


@api_view(["POST"])
@permission_classes([AllowAny])
def sasapay_b2c_callback(request):
    sp = handle_b2c_callback(request.data if isinstance(request.data, dict) else {})
    return Response({"ResultCode": 0, "ResultDesc": "Accepted",
                     "matched": bool(sp), "status": sp.status if sp else None})