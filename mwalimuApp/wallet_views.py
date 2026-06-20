"""Wallet HTTP endpoints."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
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
        if self.action in ("release", "refund", "admin_overview"):
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