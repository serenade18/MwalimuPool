from django.contrib.auth import get_user_model, authenticate
from rest_framework import serializers
from rest_framework_simplejwt.views import TokenObtainPairView

from mwalimuApp.models import TeacherProfile, Rating, Payment, Booking, JobApplication, Dispute, JobPosting, \
    SchoolProfile
from mwalimuApp.models import Wallet, WalletTransaction, SasaPayTransaction, Escrow

User = get_user_model()


# ─── User / Auth ─────────────────────────────────────────────────────────────

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            "id", "email", "name", "phone", "user_type",
            "account_status", "moderation_notes", "password",
            "is_active", "added_on",
        ]
        read_only_fields = ["id", "added_on"]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class AuthUserSerializer(serializers.ModelSerializer):
    """Lightweight user info returned in auth responses — matches frontend AuthUser shape."""

    class Meta:
        model = User
        fields = ["id", "email", "name", "phone", "user_type", "account_status"]


# ─── Teacher Profile ─────────────────────────────────────────────────────────

class TeacherProfileSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.name", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    account_status = serializers.CharField(source="user.account_status", read_only=True)
    tsc_cert_url = serializers.FileField(required=False, allow_null=True, use_url=True)
    degree_url = serializers.FileField(required=False, allow_null=True, use_url=True)
    national_id_url = serializers.FileField(required=False, allow_null=True, use_url=True)

    class Meta:
        model = TeacherProfile
        fields = [
            "id", "user", "user_email", "user_name", "user_phone",
            "full_name", "subjects", "counties", "availability",
            "rate_per_session", "bio", "tsc_cert_url", "degree_url",
            "national_id_url", "vetting_status", "is_verified",
            "rating_avg", "session_count", "account_status",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "is_verified", "rating_avg", "session_count",
            "created_at", "updated_at",
        ]


# ─── School Profile ───────────────────────────────────────────────────────────

class SchoolProfileSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.name", read_only=True)

    class Meta:
        model = SchoolProfile
        fields = [
            "id", "user", "user_email", "user_name",
            "school_name", "knec_code", "county",
            "headteacher_name", "email", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ─── Job Postings ─────────────────────────────────────────────────────────────

class JobPostingSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()

    class Meta:
        model = JobPosting
        fields = [
            "id", "school", "school_name", "subject", "grade_level",
            "sessions_per_week", "preferred_days", "duration_weeks",
            "budget_per_session", "status", "description",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "school", "created_at", "updated_at"]

    def get_school_name(self, obj):
        profile = getattr(obj.school, "school_profile", None)
        return profile.school_name if profile else obj.school.name


class JobApplicationSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source="teacher.name", read_only=True)
    job_subject = serializers.CharField(source="job.subject", read_only=True)

    class Meta:
        model = JobApplication
        fields = [
            "id", "job", "job_subject", "teacher", "teacher_name",
            "status", "cover_note", "created_at",
        ]
        read_only_fields = ["id", "teacher", "created_at"]


# ─── Bookings ─────────────────────────────────────────────────────────────────

class BookingSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()
    teacher_name = serializers.CharField(source="teacher.name", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id", "school", "school_name", "teacher", "teacher_name",
            "subject", "session_date", "session_time", "location_type",
            "location_detail", "status", "payment_status",
            "gross_amount", "commission", "net_payout",
            "teacher_marked_complete", "school_confirmed_complete",
            "cancellation_reason", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "commission", "net_payout",
            "teacher_marked_complete", "school_confirmed_complete",
            "created_at", "updated_at",
        ]

    def get_school_name(self, obj):
        profile = getattr(obj.school, "school_profile", None)
        return profile.school_name if profile else obj.school.name


# ─── Payments ─────────────────────────────────────────────────────────────────

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id", "booking", "mpesa_ref", "stk_checkout_id",
            "amount", "status", "payment_type", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ─── Ratings ─────────────────────────────────────────────────────────────────

class RatingSerializer(serializers.ModelSerializer):
    rater_name = serializers.CharField(source="rater.name", read_only=True)
    ratee_name = serializers.CharField(source="ratee.name", read_only=True)

    class Meta:
        model = Rating
        fields = [
            "id", "booking", "rater", "rater_name",
            "ratee", "ratee_name", "stars", "comment", "created_at",
        ]
        read_only_fields = ["id", "rater", "created_at"]

    def validate_stars(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("Stars must be between 1 and 5.")
        return value


# ─── Disputes ────────────────────────────────────────────────────────────────

class DisputeSerializer(serializers.ModelSerializer):
    raised_by_name = serializers.CharField(source="raised_by.name", read_only=True)

    class Meta:
        model = Dispute
        fields = [
            "id", "booking", "raised_by", "raised_by_name",
            "reason", "description", "evidence_url", "status",
            "resolution", "admin_notes", "resolved_by",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "raised_by", "status", "resolution",
            "admin_notes", "resolved_by", "created_at", "updated_at",
        ]


# ─── Wallet ──────────────────────────────────────────────────────────────────

class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = [
            "id", "owner", "owner_type", "available_balance",
            "pending_balance", "currency", "created_at", "updated_at",
        ]
        read_only_fields = fields


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = [
            "id", "tx_type", "direction", "amount", "balance_after",
            "status", "reference", "description", "related_booking",
            "metadata", "created_at",
        ]
        read_only_fields = fields


class SasaPayTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SasaPayTransaction
        fields = [
            "id", "kind", "merchant_reference", "checkout_request_id",
            "provider_reference", "phone", "amount", "status", "created_at",
        ]
        read_only_fields = fields


class EscrowSerializer(serializers.ModelSerializer):
    class Meta:
        model = Escrow
        fields = [
            "id", "booking", "amount", "fee_amount", "status",
            "held_at", "released_at",
        ]
        read_only_fields = fields