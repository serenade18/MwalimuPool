import base64
import requests
from django.conf import settings


# =============================================================================
# SasaPay — C2B (collections/deposits) + B2C (payouts/withdrawals)
# Docs:
#   https://docs.sasapay.app/docs/customerTobusiness
#   https://docs.sasapay.app/docs/b2c/
# =============================================================================

class SasaPayService:
    """Thin client for SasaPay's C2B and B2C REST APIs.

    Auth: OAuth2 client_credentials. The token endpoint accepts a Basic header
    of base64(client_id:client_secret) and returns a short-lived access_token
    that must be sent as `Authorization: Bearer <token>` on every call.
    """

    SANDBOX_BASE = "https://sandbox.sasapay.app/api/v1"
    LIVE_BASE = "https://api.sasapay.app/api/v1"

    # SasaPay channel codes (subset — full list in docs)
    CHANNEL_SASAPAY = "0"
    CHANNEL_MPESA = "63902"
    CHANNEL_AIRTEL = "63903"
    CHANNEL_TKASH = "63907"

    def __init__(self):
        env = getattr(settings, "SASAPAY_ENV", "sandbox")
        self.base_url = self.LIVE_BASE if env == "production" else self.SANDBOX_BASE
        self.client_id = settings.SASAPAY_CLIENT_ID
        self.client_secret = settings.SASAPAY_CLIENT_SECRET
        self.merchant_code = settings.SASAPAY_MERCHANT_CODE
        self.c2b_callback = settings.SASAPAY_C2B_CALLBACK_URL
        self.b2c_callback = settings.SASAPAY_B2C_CALLBACK_URL

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def generate_access_token(self):
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        url = f"{self.base_url}/auth/token/?grant_type=client_credentials"
        response = requests.get(
            url,
            headers={"Authorization": f"Basic {credentials}"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self.generate_access_token()}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # C2B — request funds from a customer (SasaPay user, M-Pesa, Airtel, T-Kash)
    # ------------------------------------------------------------------
    def request_payment(
        self,
        phone,
        amount,
        account_reference,
        description="Wallet top-up",
        network_code=CHANNEL_MPESA,
        currency="KES",
        callback_url=None,
        transaction_fee=0,
    ):
        """Trigger an STK push (M-Pesa/Airtel/T-Kash) or OTP (SasaPay user)."""
        url = f"{self.base_url}/payments/request-payment/"
        payload = {
            "MerchantCode": self.merchant_code,
            "NetworkCode": str(network_code),
            "PhoneNumber": str(phone),
            "TransactionDesc": description,
            "AccountReference": str(account_reference),
            "Currency": currency,
            "Amount": str(amount),
            "Transaction Fee": transaction_fee,
            "CallBackURL": callback_url or self.c2b_callback,
        }
        response = requests.post(
            url, headers=self._auth_headers(), json=payload, timeout=30
        )
        response.raise_for_status()
        return response.json()

    def process_payment(self, checkout_request_id, verification_code):
        """Submit the OTP returned to a SasaPay-registered user to finalise C2B."""
        url = f"{self.base_url}/payments/process-payment/"
        payload = {
            "MerchantCode": self.merchant_code,
            "CheckoutRequestID": checkout_request_id,
            "VerificationCode": verification_code,
        }
        response = requests.post(
            url, headers=self._auth_headers(), json=payload, timeout=30
        )
        response.raise_for_status()
        return response.json()

    def transaction_status(self, checkout_request_id=None, merchant_reference=None):
        """Query SasaPay for the current state of a C2B transaction.

        Used to actively reconcile pending top-ups when the async callback
        is delayed or never arrives (common in sandbox / behind NAT)."""
        url = f"{self.base_url}/transactions/status/"
        payload = {"MerchantCode": self.merchant_code}
        if checkout_request_id:
            payload["CheckoutRequestID"] = str(checkout_request_id)
        if merchant_reference:
            payload["MerchantTransactionReference"] = str(merchant_reference)
        response = requests.post(
            url, headers=self._auth_headers(), json=payload, timeout=30
        )
        if not response.ok:
            try:
                body = response.json()
            except Exception:
                body = response.text
            raise requests.HTTPError(
                f"SasaPay status {response.status_code}: {body}",
                response=response,
            )
        return response.json()

    # ------------------------------------------------------------------
    # B2C — disburse funds from merchant Utility account to a customer
    # ------------------------------------------------------------------
    def send_b2c(
        self,
        receiver_number,
        amount,
        merchant_transaction_reference,
        reason="Wallet withdrawal",
        channel=CHANNEL_MPESA,
        currency="KES",
        callback_url=None,
    ):
        """Send money to a customer (SasaPay/M-Pesa/Airtel/Bank).

        `receiver_number` must include the country code, e.g. 254712345678.
        Default channel is M-Pesa (63902); use "0" only if the receiver is a
        registered SasaPay user, otherwise SasaPay returns HTTP 400.
        """
        url = f"{self.base_url}/payments/b2c/"
        payload = {
            "MerchantCode": str(self.merchant_code),
            "MerchantTransactionReference": str(merchant_transaction_reference),
            "Amount": str(amount),
            "Currency": currency,
            "ReceiverNumber": str(receiver_number),
            "Channel": str(channel),
            "Reason": reason,
            "CallBackURL": callback_url or self.b2c_callback,
        }
        response = requests.post(
            url, headers=self._auth_headers(), json=payload, timeout=30
        )
        if not response.ok:
            # Surface SasaPay's actual error body — the bare HTTPError text
            # ("400 Client Error: Bad Request for url: ...") hides the real reason.
            try:
                body = response.json()
            except Exception:
                body = response.text
            raise requests.HTTPError(
                f"SasaPay B2C {response.status_code}: {body}",
                response=response,
            )
        return response.json()
