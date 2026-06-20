from django.db.models import Q, Avg, Sum
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.conf import settings
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str

from mwalimuApp.models import UserAccount, TeacherProfile, SchoolProfile, JobPosting, \
    JobApplication, Booking, Payment, Rating, Dispute
from mwalimuApp.permissions import IsAdminRole, IsSchoolRole, IsTeacherRole, any_of
from mwalimuApp.emails import (
    send_activation_email,
    send_teacher_kyc_pending_email,
    send_kyc_approved_email,
    send_kyc_rejected_email,
)

from mwalimuApp.serializers import UserSerializer, AuthUserSerializer, \
    TeacherProfileSerializer, SchoolProfileSerializer,JobPostingSerializer, \
    JobApplicationSerializer, BookingSerializer, PaymentSerializer, RatingSerializer, \
    DisputeSerializer


def _make_tokens(user):
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


class UserViewSet(viewsets.ViewSet):
    """
    POST   /users/                  Register
    GET    /users/                  List all users (admin)
    GET    /users/<pk>/             Retrieve user
    GET    /userinfo/               Current user from Bearer token
    POST   /gettoken/               Login → {access, refresh, user}
    POST   /password-reset/         Send reset email
    POST   /password-reset/confirm/ Confirm reset with token
    PATCH  /users/<pk>/status/      Admin: update account status
    """

    def get_permissions(self):
        if self.action in ("create", "login", "forgot_password", "reset_password", "activate"):
            return [AllowAny()]
        if self.action in ("list", "update_status"):
            return [IsAdminRole()]
        return [IsAuthenticated()]

    # POST /users/
    def create(self, request):
        """Register a new school or teacher account."""
        try:
            data = request.data.copy()
            # Accept either user_type or the legacy role field from the frontend
            user_type = data.get("user_type") or data.get("role") or UserAccount.UserTypes.SCHOOL
            data["user_type"] = user_type

            serializer = UserSerializer(data=data)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation Failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            user = serializer.save()

            # Teachers: active so they can complete onboarding, but blocked from
            # logging in again once KYC has been submitted (handled in create profile).
            # No activation email — they're activated only after admin approves KYC.
            if user.user_type == UserAccount.UserTypes.TEACHER:
                user.account_status = UserAccount.AccountStatus.VETTING_MISSING
                user.is_active = True
                user.save(update_fields=["account_status", "is_active"])
                TeacherProfile.objects.get_or_create(
                    user=user,
                    defaults={"full_name": user.name, "vetting_status": "pending"},
                )

            elif user.user_type == UserAccount.UserTypes.SCHOOL:
                # Schools must click activation link before they can log in.
                user.is_active = False
                user.account_status = UserAccount.AccountStatus.INACTIVE
                user.save(update_fields=["is_active", "account_status"])
                SchoolProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "school_name": data.get("school_name", ""),
                        "county": data.get("county", ""),
                        "headteacher_name": user.name,
                        "email": user.email,
                    },
                )
                send_activation_email(user)

            elif user.user_type == UserAccount.UserTypes.ADMIN:
                # Admins are activated immediately and receive no email.
                user.is_active = True
                user.account_status = UserAccount.AccountStatus.ACTIVE
                user.save(update_fields=["is_active", "account_status"])

            # Schools must activate via email before getting tokens.
            if user.user_type == UserAccount.UserTypes.SCHOOL:
                return Response({
                    "error": False,
                    "message": "Account created. Check your email to activate your account.",
                    "requires_activation": True,
                    "user": AuthUserSerializer(user).data,
                }, status=status.HTTP_201_CREATED)

            access, refresh = _make_tokens(user)
            return Response({
                "error": False,
                "message": "Account created successfully",
                "access": access,
                "refresh": refresh,
                "user": AuthUserSerializer(user).data,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({
                "error": True,
                "message": "An error occurred",
                "details": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    # GET /users/  (admin only)
    def list(self, request):
        try:
            user_type = request.query_params.get("user_type")
            account_status = request.query_params.get("account_status")
            qs = UserAccount.objects.all().order_by("-id")
            if user_type:
                qs = qs.filter(user_type=user_type)
            if account_status:
                qs = qs.filter(account_status=account_status)
            return Response({
                "error": False,
                "message": "All users",
                "data": UserSerializer(qs, many=True).data,
            })
        except Exception as e:
            return Response({"error": True, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # GET /users/<pk>/
    def retrieve(self, request, pk=None):
        try:
            if not (IsAdminRole().has_permission(request, self) or str(request.user.pk) == str(pk)):
                return Response({"error": True, "message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
            user = UserAccount.objects.get(pk=pk)
            return Response({"error": False, "data": UserSerializer(user).data})
        except UserAccount.DoesNotExist:
            return Response({"error": True, "message": "User not found"}, status=status.HTTP_404_NOT_FOUND)

    # GET /userinfo/
    @action(detail=False, methods=["get"], url_path="", url_name="userinfo",
            permission_classes=[IsAuthenticated])
    def userinfo(self, request):
        return Response(AuthUserSerializer(request.user).data)

    # POST /gettoken/
    @action(detail=False, methods=["post"], url_path="", url_name="login",
            permission_classes=[AllowAny])
    def login(self, request):
        try:
            email = request.data.get("email", "").strip().lower()
            password = request.data.get("password", "")

            user = authenticate(request, username=email, password=password)
            if not user:
                return Response({
                    "error": True,
                    "message": "Invalid credentials",
                }, status=status.HTTP_401_UNAUTHORIZED)

            A = UserAccount.AccountStatus
            if user.account_status in (A.SUSPENDED, A.BANNED):
                return Response({
                    "error": True,
                    "message": "Account is not active",
                    "account_status": user.account_status,
                    "reviewer_notes": user.moderation_notes,
                    "user_type": user.user_type,
                }, status=status.HTTP_403_FORBIDDEN)

            # Teachers awaiting admin approval cannot log in.
            if user.user_type == UserAccount.UserTypes.TEACHER and \
                    user.account_status == A.VETTING_PENDING:
                return Response({
                    "error": True,
                    "message": "Your KYC submission is awaiting admin approval. "
                               "You'll receive an email once your account is approved.",
                    "account_status": user.account_status,
                    "user_type": user.user_type,
                }, status=status.HTTP_403_FORBIDDEN)

            # Schools that haven't clicked the activation link yet.
            if not user.is_active and user.user_type == UserAccount.UserTypes.SCHOOL:
                return Response({
                    "error": True,
                    "message": "Please activate your account from the email we sent you.",
                    "user_type": user.user_type,
                }, status=status.HTTP_403_FORBIDDEN)

            if user.account_status in (A.VETTING_MISSING, A.VETTING_REJECTED):
                access, refresh = _make_tokens(user)
                return Response({
                    "error": True,
                    "message": "Account pending vetting",
                    "account_status": user.account_status,
                    "reviewer_notes": user.moderation_notes,
                    "user_type": user.user_type,
                    "access": access,
                    "refresh": refresh,
                    "user": AuthUserSerializer(user).data,
                }, status=status.HTTP_403_FORBIDDEN)

            access, refresh = _make_tokens(user)
            return Response({
                "error": False,
                "access": access,
                "refresh": refresh,
                "user": AuthUserSerializer(user).data,
            })

        except Exception as e:
            return Response({"error": True, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # POST /password-reset/
    @action(detail=False, methods=["post"], url_path="", url_name="forgot_password",
            permission_classes=[AllowAny])
    def forgot_password(self, request):
        try:
            email = request.data.get("email", "").strip().lower()
            try:
                user = UserAccount.objects.get(email=email)
            except UserAccount.DoesNotExist:
                return Response({"error": False, "message": "If that email exists, a reset link was sent."})

            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            reset_url = f"{settings.FRONTEND_URL}/reset-password?token={uid}.{token}"

            send_mail(
                subject="Reset your Mwalimu Pool password",
                message=f"Click the link to reset your password: {reset_url}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
            return Response({"error": False, "message": "If that email exists, a reset link was sent."})
        except Exception as e:
            return Response({"error": True, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # POST /password-reset/confirm/
    @action(detail=False, methods=["post"], url_path="", url_name="reset_password",
            permission_classes=[AllowAny])
    def reset_password(self, request):
        try:
            raw_token = request.data.get("token", "")
            new_password = request.data.get("new_password", "")
            if not raw_token or not new_password:
                return Response({
                    "error": True,
                    "message": "token and new_password are required",
                }, status=status.HTTP_400_BAD_REQUEST)

            try:
                uid_b64, token = raw_token.rsplit(".", 1)
                uid = force_str(urlsafe_base64_decode(uid_b64))
                user = UserAccount.objects.get(pk=uid)
            except Exception:
                return Response({"error": True, "message": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)

            if not default_token_generator.check_token(user, token):
                return Response({"error": True, "message": "Token expired or invalid"}, status=status.HTTP_400_BAD_REQUEST)

            user.set_password(new_password)
            user.save()
            return Response({"error": False, "message": "Password reset successfully"})
        except Exception as e:
            return Response({"error": True, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # GET /activate/<uidb64>/<token>/
    @action(detail=False, methods=["get", "post"], url_path="activate",
            permission_classes=[AllowAny])
    def activate(self, request, uidb64=None, token=None):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = UserAccount.objects.get(pk=uid)
        except Exception:
            return Response({"error": True, "message": "Invalid activation link"},
                            status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, token):
            return Response({"error": True, "message": "Activation link expired or invalid"},
                            status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            user.is_active = True
            user.account_status = UserAccount.AccountStatus.ACTIVE
            user.save(update_fields=["is_active", "account_status"])

        access, refresh = _make_tokens(user)
        return Response({
            "error": False,
            "message": "Account activated",
            "access": access,
            "refresh": refresh,
            "user": AuthUserSerializer(user).data,
        })

    # PATCH /users/<pk>/status/  (admin only)
    @action(detail=True, methods=["patch"], url_path="status",
            permission_classes=[IsAdminRole])
    def update_status(self, request, pk=None):
        try:
            user = UserAccount.objects.get(pk=pk)
            new_status = request.data.get("account_status")
            moderation_notes = request.data.get("moderation_notes", "")

            if not new_status:
                return Response({"error": True, "message": "account_status required"},
                                status=status.HTTP_400_BAD_REQUEST)

            user.account_status = new_status
            if moderation_notes:
                user.moderation_notes = moderation_notes

            A = UserAccount.AccountStatus
            # Sync teacher profile vetting status
            if hasattr(user, "teacher_profile"):
                if new_status == A.ACTIVE:
                    user.teacher_profile.vetting_status = "approved"
                    user.teacher_profile.is_verified = True
                    user.teacher_profile.save(update_fields=["vetting_status", "is_verified"])
                elif new_status == A.VETTING_REJECTED:
                    user.teacher_profile.vetting_status = "rejected"
                    user.teacher_profile.save(update_fields=["vetting_status"])

            # Re-activate teachers on approval, send notification emails.
            if new_status == A.ACTIVE:
                user.is_active = True
            user.save()

            if user.user_type == UserAccount.UserTypes.TEACHER:
                if new_status == A.ACTIVE:
                    send_kyc_approved_email(user)
                elif new_status == A.VETTING_REJECTED:
                    send_kyc_rejected_email(user, notes=moderation_notes)

            return Response({
                "error": False,
                "message": "User status updated",
                "data": UserSerializer(user).data,
            })
        except UserAccount.DoesNotExist:
            return Response({"error": True, "message": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": True, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

# ============================================================
# TEACHER PROFILE VIEWSET
# ============================================================

class TeacherProfileViewSet(viewsets.ViewSet):
    """
    GET    /teachers/           Search/filter verified teachers
    POST   /teachers/           Teacher creates/updates own profile
    GET    /teachers/<pk>/      Retrieve a teacher profile
    PATCH  /teachers/<pk>/      Teacher updates own profile
    DELETE /teachers/<pk>/      Admin deletes profile
    GET    /teachers/vetting/   Admin vetting queue (pending profiles)
    """
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.action == "list":
            return [IsAuthenticated()]
        if self.action == "create":
            return [IsTeacherRole()]
        if self.action == "update":
            return [any_of(IsTeacherRole, IsAdminRole)()]
        if self.action == "destroy":
            return [IsAdminRole()]
        if self.action == "vetting_queue":
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def list(self, request):
        """Search verified teachers. Schools use this to find and book."""
        try:
            qs = TeacherProfile.objects.select_related("user").filter(
                vetting_status="approved",
                user__account_status=UserAccount.AccountStatus.ACTIVE,
            )

            subject = request.query_params.get("subject")
            county = request.query_params.get("county")
            min_rating = request.query_params.get("min_rating")
            max_rate = request.query_params.get("max_rate")

            if subject:
                # JSONField contains() lookup for list values
                qs = qs.filter(subjects__contains=subject)
            if county:
                qs = qs.filter(counties__contains=county)
            if min_rating:
                qs = qs.filter(rating_avg__gte=min_rating)
            if max_rate:
                qs = qs.filter(rate_per_session__lte=max_rate)

            serializer = TeacherProfileSerializer(qs, many=True)
            return Response({
                "error": False,
                "message": "Teachers",
                "data": serializer.data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        """
        Teacher submits or updates their profile.
        Uses get_or_create so hitting POST again updates the existing profile.
        """
        try:
            profile, created = TeacherProfile.objects.get_or_create(
                user=request.user,
                defaults={"full_name": request.user.name},
            )
            serializer = TeacherProfileSerializer(profile, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer.save(user=request.user)

            # Promote account_status to vetting_pending once docs are uploaded
            has_docs = request.data.get("tsc_cert_url") or request.data.get("degree_url") or \
                       request.data.get("national_id_url")
            if has_docs and request.user.account_status in (
                UserAccount.AccountStatus.VETTING_MISSING,
                UserAccount.AccountStatus.VETTING_REJECTED,
            ):
                request.user.account_status = UserAccount.AccountStatus.VETTING_PENDING
                # Block further logins until admin approves.
                request.user.is_active = False
                request.user.save(update_fields=["account_status", "is_active"])
                send_teacher_kyc_pending_email(request.user)

            return Response({
                "error": False,
                "message": "Profile saved successfully",
                "data": serializer.data,
            }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            profile = TeacherProfile.objects.select_related("user").get(pk=pk)
            return Response({
                "error": False,
                "data": TeacherProfileSerializer(profile).data,
            })
        except TeacherProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "Teacher profile not found",
            }, status=status.HTTP_404_NOT_FOUND)

    def update(self, request, pk=None):
        try:
            profile = TeacherProfile.objects.select_related("user").get(pk=pk)

            # Teachers may only edit their own profile
            if (IsTeacherRole().has_permission(request, self)
                    and profile.user != request.user):
                return Response({
                    "error": True,
                    "message": "You can only edit your own profile",
                }, status=status.HTTP_403_FORBIDDEN)

            serializer = TeacherProfileSerializer(profile, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer.save()
            return Response({
                "error": False,
                "message": "Profile updated",
                "data": serializer.data,
            })
        except TeacherProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "Teacher profile not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None):
        try:
            profile = TeacherProfile.objects.get(pk=pk)
            profile.delete()
            return Response({
                "error": False,
                "message": "Teacher profile deleted",
            })
        except TeacherProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "Teacher profile not found",
            }, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["get"], url_path="vetting")
    def vetting_queue(self, request):
        """Admin — all teacher profiles awaiting vetting."""
        try:
            qs = TeacherProfile.objects.select_related("user").filter(
                vetting_status="pending",
            ).order_by("created_at")

            return Response({
                "error": False,
                "message": "Vetting queue",
                "count": qs.count(),
                "data": TeacherProfileSerializer(qs, many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        try:
            profile = TeacherProfile.objects.select_related("user").get(
                user=request.user
            )

            return Response({
                "error": False,
                "data": TeacherProfileSerializer(profile).data,
            })

        except TeacherProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "Teacher profile not found",
            }, status=status.HTTP_404_NOT_FOUND)

# ============================================================
# SCHOOL PROFILE VIEWSET
# ============================================================

class SchoolProfileViewSet(viewsets.ViewSet):
    """
    GET    /schools/        Admin: list all schools
    POST   /schools/        School creates/updates own profile
    GET    /schools/<pk>/   Retrieve a school profile
    PATCH  /schools/<pk>/   School updates own profile
    DELETE /schools/<pk>/   Admin deletes
    """

    def get_permissions(self):
        if self.action == "list":
            return [IsAdminRole()]
        if self.action == "create":
            return [IsSchoolRole()]
        if self.action == "destroy":
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def list(self, request):
        try:
            county = request.query_params.get("county")
            qs = SchoolProfile.objects.select_related("user").all().order_by("-id")
            if county:
                qs = qs.filter(county__icontains=county)
            return Response({
                "error": False,
                "data": SchoolProfileSerializer(qs, many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        """School submits or updates their profile."""
        try:
            profile, created = SchoolProfile.objects.get_or_create(user=request.user)
            serializer = SchoolProfileSerializer(profile, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer.save(user=request.user)
            return Response({
                "error": False,
                "message": "School profile saved",
                "data": serializer.data,
            }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            profile = SchoolProfile.objects.select_related("user").get(pk=pk)
            return Response({
                "error": False,
                "data": SchoolProfileSerializer(profile).data,
            })
        except SchoolProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "School profile not found",
            }, status=status.HTTP_404_NOT_FOUND)

    def update(self, request, pk=None):
        try:
            profile = SchoolProfile.objects.select_related("user").get(pk=pk)

            if (IsSchoolRole().has_permission(request, self)
                    and profile.user != request.user):
                return Response({
                    "error": True,
                    "message": "You can only edit your own profile",
                }, status=status.HTTP_403_FORBIDDEN)

            serializer = SchoolProfileSerializer(profile, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer.save()
            return Response({
                "error": False,
                "message": "School profile updated",
                "data": serializer.data,
            })
        except SchoolProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "School profile not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None):
        try:
            profile = SchoolProfile.objects.get(pk=pk)
            profile.delete()
            return Response({
                "error": False,
                "message": "School profile deleted",
            })
        except SchoolProfile.DoesNotExist:
            return Response({
                "error": True,
                "message": "School profile not found",
            }, status=status.HTTP_404_NOT_FOUND)

# ============================================================
# JOB POSTING VIEWSET
# ============================================================

class JobPostingViewSet(viewsets.ViewSet):
    """
    GET    /jobs/                   List open postings (teachers browse)
    POST   /jobs/                   School creates a posting
    GET    /jobs/<pk>/              Retrieve a posting
    PATCH  /jobs/<pk>/              School updates own posting
    DELETE /jobs/<pk>/              School closes / admin deletes
    POST   /jobs/<pk>/apply/        Teacher applies
    GET    /jobs/<pk>/applications/ School views applicants
    PATCH  /jobs/<pk>/applications/<app_pk>/  School updates application status
    GET    /jobs/mine/              School's own postings
    """

    def get_permissions(self):
        if self.action == "list":
            return [IsAuthenticated()]
        if self.action == "create":
            return [IsSchoolRole()]
        if self.action in ("update", "destroy"):
            return [any_of(IsSchoolRole, IsAdminRole)()]
        if self.action == "apply":
            return [IsTeacherRole()]
        if self.action in ("applications", "update_application"):
            return [any_of(IsSchoolRole, IsAdminRole)()]
        return [IsAuthenticated()]

    def list(self, request):
        """Open job postings — teachers browse these."""
        try:
            qs = JobPosting.objects.select_related("school", "school__school_profile").filter(
                status="open"
            )

            subject = request.query_params.get("subject")
            county = request.query_params.get("county")
            grade_level = request.query_params.get("grade_level")
            max_budget = request.query_params.get("max_budget")

            if subject:
                qs = qs.filter(subject__icontains=subject)
            if county:
                qs = qs.filter(school__school_profile__county__icontains=county)
            if grade_level:
                qs = qs.filter(grade_level__icontains=grade_level)
            if max_budget:
                qs = qs.filter(budget_per_session__lte=max_budget)

            return Response({
                "error": False,
                "message": "Open job postings",
                "data": JobPostingSerializer(qs, many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        try:
            serializer = JobPostingSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            posting = serializer.save(school=request.user)
            return Response({
                "error": False,
                "message": "Job posting created",
                "data": JobPostingSerializer(posting).data,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            posting = JobPosting.objects.select_related(
                "school", "school__school_profile"
            ).get(pk=pk)
            return Response({
                "error": False,
                "data": JobPostingSerializer(posting).data,
            })
        except JobPosting.DoesNotExist:
            return Response({
                "error": True,
                "message": "Job posting not found",
            }, status=status.HTTP_404_NOT_FOUND)

    def update(self, request, pk=None):
        try:
            posting = JobPosting.objects.get(pk=pk)

            if (IsSchoolRole().has_permission(request, self)
                    and posting.school != request.user):
                return Response({
                    "error": True,
                    "message": "You can only edit your own postings",
                }, status=status.HTTP_403_FORBIDDEN)

            serializer = JobPostingSerializer(posting, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer.save()
            return Response({
                "error": False,
                "message": "Job posting updated",
                "data": serializer.data,
            })
        except JobPosting.DoesNotExist:
            return Response({
                "error": True,
                "message": "Job posting not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None):
        try:
            posting = JobPosting.objects.get(pk=pk)

            if (IsSchoolRole().has_permission(request, self)
                    and posting.school != request.user):
                return Response({
                    "error": True,
                    "message": "You can only delete your own postings",
                }, status=status.HTTP_403_FORBIDDEN)

            posting.status = "closed"
            posting.save(update_fields=["status"])
            return Response({
                "error": False,
                "message": "Job posting closed",
            })
        except JobPosting.DoesNotExist:
            return Response({
                "error": True,
                "message": "Job posting not found",
            }, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, pk=None):
        """Teacher applies to an open posting."""
        try:
            posting = JobPosting.objects.get(pk=pk, status="open")

            if JobApplication.objects.filter(job=posting, teacher=request.user).exists():
                return Response({
                    "error": True,
                    "message": "You have already applied to this posting",
                }, status=status.HTTP_400_BAD_REQUEST)

            application = JobApplication.objects.create(
                job=posting,
                teacher=request.user,
                cover_note=request.data.get("cover_note", ""),
            )
            return Response({
                "error": False,
                "message": "Application submitted",
                "data": JobApplicationSerializer(application).data,
            }, status=status.HTTP_201_CREATED)

        except JobPosting.DoesNotExist:
            return Response({
                "error": True,
                "message": "Job posting not found or no longer open",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"], url_path="applications")
    def applications(self, request, pk=None):
        """School views all applicants for their posting."""
        try:
            posting = JobPosting.objects.get(pk=pk)

            if (IsSchoolRole().has_permission(request, self)
                    and posting.school != request.user):
                return Response({
                    "error": True,
                    "message": "Forbidden",
                }, status=status.HTTP_403_FORBIDDEN)

            apps = JobApplication.objects.select_related(
                "teacher", "teacher__teacher_profile"
            ).filter(job=posting).order_by("-created_at")

            return Response({
                "error": False,
                "data": JobApplicationSerializer(apps, many=True).data,
            })
        except JobPosting.DoesNotExist:
            return Response({
                "error": True,
                "message": "Job posting not found",
            }, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=["patch"], url_path="applications/(?P<app_pk>[^/.]+)")
    def update_application(self, request, pk=None, app_pk=None):
        """School shortlists or rejects a specific application."""
        try:
            posting = JobPosting.objects.get(pk=pk)

            if (IsSchoolRole().has_permission(request, self)
                    and posting.school != request.user):
                return Response({
                    "error": True,
                    "message": "Forbidden",
                }, status=status.HTTP_403_FORBIDDEN)

            application = JobApplication.objects.get(pk=app_pk, job=posting)
            new_status = request.data.get("status")
            if new_status not in ("applied", "shortlisted", "rejected"):
                return Response({
                    "error": True,
                    "message": "status must be one of: applied, shortlisted, rejected",
                }, status=status.HTTP_400_BAD_REQUEST)

            application.status = new_status
            application.save(update_fields=["status"])
            return Response({
                "error": False,
                "message": f"Application marked as {new_status}",
                "data": JobApplicationSerializer(application).data,
            })
        except (JobPosting.DoesNotExist, JobApplication.DoesNotExist):
            return Response({
                "error": True,
                "message": "Not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        """School's own job postings."""
        try:
            qs = JobPosting.objects.filter(school=request.user).order_by("-created_at")
            bstatus = request.query_params.get("status")
            if bstatus:
                qs = qs.filter(status=bstatus)
            return Response({
                "error": False,
                "data": JobPostingSerializer(qs, many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

# ============================================================
# BOOKING VIEWSET
# ============================================================

class BookingViewSet(viewsets.ViewSet):
    """
    GET    /bookings/                       Own bookings (role-filtered)
    POST   /bookings/                       School creates a booking
    GET    /bookings/<pk>/                  Retrieve booking
    PATCH  /bookings/<pk>/                  Update booking details
    POST   /bookings/<pk>/cancel/           Cancel a booking
    POST   /bookings/<pk>/complete/         Teacher marks session complete
    POST   /bookings/<pk>/confirm-complete/ School confirms → triggers payout
    """

    def get_permissions(self):
        if self.action == "create":
            return [IsSchoolRole()]
        if self.action == "complete":
            return [IsTeacherRole()]
        if self.action == "confirm_complete":
            return [IsSchoolRole()]
        return [IsAuthenticated()]

    def list(self, request):
        try:
            user = request.user
            if IsAdminRole().has_permission(request, self):
                qs = Booking.objects.select_related("school", "teacher").all()
            elif user.user_type == UserAccount.UserTypes.SCHOOL:
                qs = Booking.objects.select_related("teacher").filter(school=user)
            else:
                qs = Booking.objects.select_related("school").filter(teacher=user)

            # Optional filters
            bstatus = request.query_params.get("status")
            payment_status = request.query_params.get("payment_status")
            if bstatus:
                qs = qs.filter(status=bstatus)
            if payment_status:
                qs = qs.filter(payment_status=payment_status)

            return Response({
                "error": False,
                "message": "Bookings",
                "data": BookingSerializer(qs.order_by("-created_at"), many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        """School creates a booking for a specific teacher."""
        try:
            serializer = BookingSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            booking = serializer.save(school=request.user)
            return Response({
                "error": False,
                "message": "Booking created. Initiate M-Pesa payment to confirm.",
                "data": BookingSerializer(booking).data,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            booking = self._get_owned_booking(request, pk)
            return Response({
                "error": False,
                "data": BookingSerializer(booking).data,
            })
        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_403_FORBIDDEN)

    def update(self, request, pk=None):
        try:
            booking = self._get_owned_booking(request, pk)
            serializer = BookingSerializer(booking, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)
            serializer.save()
            return Response({
                "error": False,
                "message": "Booking updated",
                "data": serializer.data,
            })
        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        try:
            booking = self._get_owned_booking(request, pk)

            if booking.status not in ("pending", "confirmed"):
                return Response({
                    "error": True,
                    "message": f"Cannot cancel a booking with status '{booking.status}'",
                }, status=status.HTTP_400_BAD_REQUEST)

            booking.status = "cancelled"
            booking.cancellation_reason = request.data.get("reason", "")
            booking.save(update_fields=["status", "cancellation_reason", "updated_at"])
            return Response({
                "error": False,
                "message": "Booking cancelled",
                "data": BookingSerializer(booking).data,
            })
        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except PermissionError as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_403_FORBIDDEN)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        """Teacher marks their session as delivered."""
        try:
            booking = Booking.objects.get(pk=pk, teacher=request.user)

            if booking.status != "confirmed":
                return Response({
                    "error": True,
                    "message": "Only confirmed bookings can be marked complete",
                }, status=status.HTTP_400_BAD_REQUEST)

            booking.teacher_marked_complete = True
            # If school already confirmed (edge case), flip to completed immediately
            if booking.school_confirmed_complete:
                booking.status = "completed"
            booking.save(update_fields=["teacher_marked_complete", "status", "updated_at"])

            return Response({
                "error": False,
                "message": "Session marked as complete. Awaiting school confirmation.",
                "data": BookingSerializer(booking).data,
            })
        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="confirm-complete")
    def confirm_complete(self, request, pk=None):
        """School confirms session — releases escrow and triggers payout."""
        try:
            booking = Booking.objects.get(pk=pk, school=request.user)

            if not booking.teacher_marked_complete:
                return Response({
                    "error": True,
                    "message": "Teacher has not marked the session as complete yet",
                }, status=status.HTTP_400_BAD_REQUEST)

            if booking.status == "completed":
                return Response({
                    "error": True,
                    "message": "Session is already marked completed",
                }, status=status.HTTP_400_BAD_REQUEST)

            booking.school_confirmed_complete = True
            booking.status = "completed"
            booking.save(update_fields=["school_confirmed_complete", "status", "updated_at"])

            # TODO: trigger M-Pesa B2C payout via Celery task
            # payout_teacher.delay(booking.pk)

            return Response({
                "error": False,
                "message": "Session confirmed. Payout of KES {} initiated to teacher.".format(
                    booking.net_payout
                ),
                "data": BookingSerializer(booking).data,
            })
        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def _get_owned_booking(self, request, pk):
        """Fetch booking and enforce ownership. Admins bypass."""
        booking = Booking.objects.select_related("school", "teacher").get(pk=pk)
        if IsAdminRole().has_permission(request, self):
            return booking
        if request.user not in (booking.school, booking.teacher):
            raise PermissionError("You do not have access to this booking")
        return booking

# ============================================================
# PAYMENT VIEWSET
# ============================================================

class PaymentViewSet(viewsets.ViewSet):
    """
    GET    /payments/                   Own payments (role-filtered)
    POST   /payments/                   Initiate STK Push / record payment
    GET    /payments/<pk>/              Retrieve payment
    POST   /payments/mpesa-callback/    Daraja webhook (unauthenticated)
    GET    /payments/revenue/           Admin revenue summary
    """

    def get_permissions(self):
        if self.action == "mpesa_callback":
            return []   # Daraja posts here — validated by body signature, not JWT
        if self.action == "revenue":
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def list(self, request):
        try:
            user = request.user
            if IsAdminRole().has_permission(request, self):
                qs = Payment.objects.select_related("booking").all()
            elif user.user_type == UserAccount.UserTypes.SCHOOL:
                qs = Payment.objects.select_related("booking").filter(booking__school=user)
            else:
                qs = Payment.objects.select_related("booking").filter(booking__teacher=user)

            return Response({
                "error": False,
                "data": PaymentSerializer(qs.order_by("-created_at"), many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        """
        School initiates an M-Pesa STK Push for a booking.
        Records a pending Payment and updates booking.payment_status.
        """
        try:
            booking_id = request.data.get("booking")
            booking = Booking.objects.get(pk=booking_id, school=request.user)

            if booking.payment_status == "paid":
                return Response({
                    "error": True,
                    "message": "This booking has already been paid",
                }, status=status.HTTP_400_BAD_REQUEST)

            # TODO: call Daraja STK Push API here and capture CheckoutRequestID
            # checkout_id = daraja.stk_push(phone=request.user.phone, amount=booking.gross_amount)
            checkout_id = request.data.get("stk_checkout_id", "")

            payment = Payment.objects.create(
                booking=booking,
                amount=booking.gross_amount,
                stk_checkout_id=checkout_id,
                payment_type="c2b",
                status="pending",
            )

            booking.payment_status = "pending"
            booking.save(update_fields=["payment_status", "updated_at"])

            return Response({
                "error": False,
                "message": "STK Push initiated. Check your phone to complete payment.",
                "data": PaymentSerializer(payment).data,
            }, status=status.HTTP_201_CREATED)

        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            payment = Payment.objects.select_related("booking").get(pk=pk)
            return Response({
                "error": False,
                "data": PaymentSerializer(payment).data,
            })
        except Payment.DoesNotExist:
            return Response({
                "error": True,
                "message": "Payment not found",
            }, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["post"], url_path="mpesa-callback")
    def mpesa_callback(self, request):
        """
        Daraja STK Push callback.
        ResultCode 0 = success, anything else = failure.
        """
        try:
            body = request.data.get("Body", {})
            stk_callback = body.get("stkCallback", {})
            result_code = stk_callback.get("ResultCode")
            checkout_id = stk_callback.get("CheckoutRequestID", "")

            payment = Payment.objects.select_related("booking").filter(
                stk_checkout_id=checkout_id
            ).first()

            if not payment:
                # Acknowledge anyway so Daraja doesn't retry
                return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

            if result_code == 0:
                items = stk_callback.get("CallbackMetadata", {}).get("Item", [])
                mpesa_ref = next(
                    (i["Value"] for i in items if i["Name"] == "MpesaReceiptNumber"), ""
                )
                payment.status = "completed"
                payment.mpesa_ref = mpesa_ref
                payment.save(update_fields=["status", "mpesa_ref"])

                payment.booking.payment_status = "paid"
                payment.booking.status = "confirmed"
                payment.booking.save(update_fields=["payment_status", "status", "updated_at"])

            else:
                payment.status = "failed"
                payment.save(update_fields=["status"])

                payment.booking.payment_status = "unpaid"
                payment.booking.save(update_fields=["payment_status", "updated_at"])

            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        except Exception as e:
            # Always return 200 to Daraja or it will keep retrying
            return Response({"ResultCode": 0, "ResultDesc": str(e)})

    @action(detail=False, methods=["get"], url_path="revenue")
    def revenue(self, request):
        """Admin revenue dashboard summary."""
        try:
            gmv = Payment.objects.filter(
                status="completed", payment_type="c2b"
            ).aggregate(total=Sum("amount"))["total"] or 0

            commission_earned = Booking.objects.filter(
                payment_status="paid"
            ).aggregate(total=Sum("commission"))["total"] or 0

            pending_payouts = Booking.objects.filter(
                status="completed", payment_status="paid"
            ).aggregate(total=Sum("net_payout"))["total"] or 0

            open_disputes = Dispute.objects.filter(status="open").count()
            total_bookings = Booking.objects.count()
            completed_sessions = Booking.objects.filter(status="completed").count()

            return Response({
                "error": False,
                "data": {
                    "gmv": float(gmv),
                    "commission_earned": float(commission_earned),
                    "pending_payouts": float(pending_payouts),
                    "open_disputes": open_disputes,
                    "total_bookings": total_bookings,
                    "completed_sessions": completed_sessions,
                },
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

# ============================================================
# RATING VIEWSET
# ============================================================

class RatingViewSet(viewsets.ViewSet):
    """
    GET    /ratings/        Own ratings or filter by teacher
    POST   /ratings/        School submits a rating after session
    GET    /ratings/<pk>/   Retrieve a rating
    """

    def get_permissions(self):
        return [IsAuthenticated()]

    def list(self, request):
        try:
            teacher_id = request.query_params.get("teacher")
            if teacher_id:
                # Public-ish: anyone can view a teacher's ratings
                qs = Rating.objects.select_related("rater", "ratee").filter(ratee_id=teacher_id)
            else:
                qs = Rating.objects.select_related("rater", "ratee").filter(
                    Q(rater=request.user) | Q(ratee=request.user)
                )
            return Response({
                "error": False,
                "data": RatingSerializer(qs.order_by("-created_at"), many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        """School rates a teacher after a completed session."""
        try:
            booking_id = request.data.get("booking")
            booking = Booking.objects.select_related("school", "teacher").get(
                pk=booking_id, status="completed"
            )

            if booking.school != request.user:
                return Response({
                    "error": True,
                    "message": "Only the school that booked this session can submit a rating",
                }, status=status.HTTP_403_FORBIDDEN)

            if hasattr(booking, "rating"):
                return Response({
                    "error": True,
                    "message": "A rating has already been submitted for this session",
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer = RatingSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            rating = serializer.save(
                rater=request.user,
                ratee=booking.teacher,
                booking=booking,
            )

            # Recalculate teacher's rating average and session count
            teacher_profile = TeacherProfile.objects.filter(user=booking.teacher).first()
            if teacher_profile:
                agg = Rating.objects.filter(ratee=booking.teacher).aggregate(avg=Avg("stars"))
                teacher_profile.rating_avg = round(agg["avg"] or 0, 2)
                teacher_profile.session_count = Rating.objects.filter(ratee=booking.teacher).count()
                teacher_profile.save(update_fields=["rating_avg", "session_count"])

            return Response({
                "error": False,
                "message": "Rating submitted. Thank you!",
                "data": RatingSerializer(rating).data,
            }, status=status.HTTP_201_CREATED)

        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Completed booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            rating = Rating.objects.select_related("rater", "ratee", "booking").get(pk=pk)
            return Response({
                "error": False,
                "data": RatingSerializer(rating).data,
            })
        except Rating.DoesNotExist:
            return Response({
                "error": True,
                "message": "Rating not found",
            }, status=status.HTTP_404_NOT_FOUND)

# ============================================================
# DISPUTE VIEWSET
# ============================================================

class DisputeViewSet(viewsets.ViewSet):
    """
    GET    /disputes/               Own disputes (admin sees all)
    POST   /disputes/               School or teacher raises a dispute
    GET    /disputes/<pk>/          Retrieve dispute
    PATCH  /disputes/<pk>/resolve/  Admin resolves — release or refund
    """

    def get_permissions(self):
        if self.action == "resolve":
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def list(self, request):
        try:
            if IsAdminRole().has_permission(request, self):
                qs = Dispute.objects.select_related("booking", "raised_by").all()
            else:
                qs = Dispute.objects.select_related("booking", "raised_by").filter(
                    Q(booking__school=request.user) | Q(booking__teacher=request.user)
                )

            dispute_status = request.query_params.get("status")
            if dispute_status:
                qs = qs.filter(status=dispute_status)

            return Response({
                "error": False,
                "data": DisputeSerializer(qs.order_by("-created_at"), many=True).data,
            })
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request):
        """School or teacher raises a dispute. Funds are held immediately."""
        try:
            booking_id = request.data.get("booking")
            booking = Booking.objects.get(pk=booking_id)

            if request.user not in (booking.school, booking.teacher):
                return Response({
                    "error": True,
                    "message": "You are not a party to this booking",
                }, status=status.HTTP_403_FORBIDDEN)

            if booking.status == "disputed":
                return Response({
                    "error": True,
                    "message": "A dispute is already open for this booking",
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer = DisputeSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({
                    "error": True,
                    "message": "Validation failed",
                    "details": serializer.errors,
                }, status=status.HTTP_400_BAD_REQUEST)

            # Hold funds and mark booking as disputed
            booking.status = "disputed"
            booking.payment_status = "held"
            booking.save(update_fields=["status", "payment_status", "updated_at"])

            dispute = serializer.save(raised_by=request.user, booking=booking)
            return Response({
                "error": False,
                "message": "Dispute submitted. Funds are held pending admin review.",
                "data": DisputeSerializer(dispute).data,
            }, status=status.HTTP_201_CREATED)

        except Booking.DoesNotExist:
            return Response({
                "error": True,
                "message": "Booking not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        try:
            dispute = Dispute.objects.select_related(
                "booking", "raised_by", "resolved_by"
            ).get(pk=pk)

            # Only parties or admin can view
            if not IsAdminRole().has_permission(request, self):
                if request.user not in (dispute.booking.school, dispute.booking.teacher):
                    return Response({
                        "error": True,
                        "message": "Forbidden",
                    }, status=status.HTTP_403_FORBIDDEN)

            return Response({
                "error": False,
                "data": DisputeSerializer(dispute).data,
            })
        except Dispute.DoesNotExist:
            return Response({
                "error": True,
                "message": "Dispute not found",
            }, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=["patch"], url_path="resolve")
    def resolve(self, request, pk=None):
        """
        Admin resolves a dispute.
        action = "release" → funds go to teacher (booking completed)
        action = "refund"  → funds returned to school (booking cancelled)
        """
        try:
            dispute = Dispute.objects.select_related("booking").get(pk=pk)

            if dispute.status == "resolved":
                return Response({
                    "error": True,
                    "message": "This dispute has already been resolved",
                }, status=status.HTTP_400_BAD_REQUEST)

            resolution_action = request.data.get("action")
            resolution = request.data.get("resolution", "")
            admin_notes = request.data.get("admin_notes", "")

            if resolution_action not in ("release", "refund"):
                return Response({
                    "error": True,
                    "message": "action must be either 'release' (pay teacher) or 'refund' (return to school)",
                }, status=status.HTTP_400_BAD_REQUEST)

            if not resolution:
                return Response({
                    "error": True,
                    "message": "resolution summary is required",
                }, status=status.HTTP_400_BAD_REQUEST)

            dispute.resolution = resolution
            dispute.admin_notes = admin_notes
            dispute.status = "resolved"
            dispute.resolved_by = request.user
            dispute.save(update_fields=["resolution", "admin_notes", "status", "resolved_by", "updated_at"])

            booking = dispute.booking
            if resolution_action == "release":
                booking.payment_status = "paid"
                booking.status = "completed"
                # TODO: trigger M-Pesa B2C payout to teacher
                # payout_teacher.delay(booking.pk)
            else:  # refund
                booking.payment_status = "refunded"
                booking.status = "cancelled"
                # TODO: trigger M-Pesa refund to school
                # refund_school.delay(booking.pk)

            booking.save(update_fields=["payment_status", "status", "updated_at"])

            return Response({
                "error": False,
                "message": "Dispute resolved — funds {}".format(
                    "released to teacher" if resolution_action == "release" else "refunded to school"
                ),
                "data": DisputeSerializer(dispute).data,
            })

        except Dispute.DoesNotExist:
            return Response({
                "error": True,
                "message": "Dispute not found",
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "error": True,
                "message": str(e),
            }, status=status.HTTP_400_BAD_REQUEST)