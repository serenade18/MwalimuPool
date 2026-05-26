from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager


# ============================
# USER MODEL
# ============================

class UserAccountManager(BaseUserManager):
    def create_user(self, email, name, phone, password=None, user_type=None):
        if not email:
            raise ValueError("Users must have an email address")

        email = self.normalize_email(email).lower()

        user = self.model(
            email=email,
            name=name,
            phone=phone,
            user_type=user_type or UserAccount.UserTypes.SCHOOL,
        )

        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, phone, password=None):
        user = self.create_user(
            email=email,
            name=name,
            phone=phone,
            password=password,
            user_type=UserAccount.UserTypes.ADMIN,
        )
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        user.save(using=self._db)
        return user


class UserAccount(AbstractBaseUser, PermissionsMixin):
    class UserTypes(models.TextChoices):
        SCHOOL = "school", "School"
        TEACHER = "teacher", "Teacher"
        ADMIN = "admin", "Admin"

    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        BANNED = "banned", "Banned"
        VETTING_MISSING = "vetting_missing", "Vetting Missing"
        VETTING_PENDING = "vetting_pending", "Vetting Pending"
        VETTING_REJECTED = "vetting_rejected", "Vetting Rejected"

    email = models.EmailField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
    user_type = models.CharField(
        max_length=20,
        choices=UserTypes.choices,
        default=UserTypes.SCHOOL,
    )

    is_active = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    account_status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
    )
    moderation_notes = models.TextField(blank=True, default="")
    added_on = models.DateTimeField(auto_now_add=True)

    objects = UserAccountManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name", "phone"]

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.email} ({self.user_type})"

# ============================
# TEACHER PROFILE MODEL
# ============================

class TeacherProfile(models.Model):
    VETTING_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    user = models.OneToOneField(UserAccount, on_delete=models.CASCADE, related_name="teacher_profile")
    full_name = models.CharField(max_length=255)
    subjects = models.JSONField(default=list, blank=True)
    counties = models.JSONField(default=list, blank=True)
    availability = models.JSONField(default=list, blank=True)
    rate_per_session = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    bio = models.TextField(blank=True)
    tsc_cert_url = models.URLField(blank=True, null=True)
    degree_url = models.URLField(blank=True, null=True)
    national_id_url = models.URLField(blank=True, null=True)
    vetting_status = models.CharField(max_length=20, choices=VETTING_STATUS_CHOICES, default="pending")
    is_verified = models.BooleanField(default=False)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    session_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teacher_profiles"

    def __str__(self):
        return f"{self.full_name} — Teacher Profile"

# ============================
# SCHOOL PROFILE MODEL
# ============================

class SchoolProfile(models.Model):
    user = models.OneToOneField(UserAccount, on_delete=models.CASCADE, related_name="school_profile")
    school_name = models.CharField(max_length=255)
    knec_code = models.CharField(max_length=50, blank=True)
    county = models.CharField(max_length=100)
    headteacher_name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_profiles"

    def __str__(self):
        return self.school_name

# ============================
# JOB POSTING MODEL
# ============================

class JobPosting(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("filled", "Filled"),
        ("closed", "Closed"),
    ]

    school = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="job_postings")
    subject = models.CharField(max_length=100)
    grade_level = models.CharField(max_length=50)
    sessions_per_week = models.PositiveIntegerField()
    preferred_days = models.JSONField(default=list, blank=True)
    duration_weeks = models.PositiveIntegerField()
    budget_per_session = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "job_postings"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} — {self.grade_level}"

# ============================
# JOB APPLICATION MODEL
# ============================

class JobApplication(models.Model):
    STATUS_CHOICES = [
        ("applied", "Applied"),
        ("shortlisted", "Shortlisted"),
        ("rejected", "Rejected"),
    ]

    job = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name="applications")
    teacher = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="applications")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="applied")
    cover_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_applications"
        unique_together = ("job", "teacher")

    def __str__(self):
        return f"{self.teacher} → {self.job}"

# ============================
# BOOKING MODEL
# ============================

class Booking(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("disputed", "Disputed"),
    ]
    PAYMENT_STATUS_CHOICES = [
        ("unpaid", "Unpaid"),
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("refunded", "Refunded"),
        ("held", "Held"),
    ]
    LOCATION_TYPE_CHOICES = [
        ("on_site", "On Site"),
        ("remote", "Remote"),
    ]

    school = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="school_bookings")
    teacher = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="teacher_bookings")
    subject = models.CharField(max_length=100)
    session_date = models.DateField()
    session_time = models.TimeField()
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES, default="on_site")
    location_detail = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="unpaid")
    gross_amount = models.DecimalField(max_digits=10, decimal_places=2)
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_payout = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    teacher_marked_complete = models.BooleanField(default=False)
    school_confirmed_complete = models.BooleanField(default=False)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    COMMISSION_RATE = 0.13

    def save(self, *args, **kwargs):
        if self.gross_amount:
            self.commission = round(float(self.gross_amount) * self.COMMISSION_RATE, 2)
            self.net_payout = round(float(self.gross_amount) - float(self.commission), 2)
        super().save(*args, **kwargs)

    class Meta:
        db_table = "bookings"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Booking #{self.pk}: {self.school} + {self.teacher}"

# ============================
# PAYMENT MODEL
# ============================

class Payment(models.Model):
    TYPE_CHOICES = [
        ("c2b", "C2B"),
        ("b2c", "B2C"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="payments")
    mpesa_ref = models.CharField(max_length=100, blank=True)
    stk_checkout_id = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payment_type = models.CharField(max_length=5, choices=TYPE_CHOICES, default="c2b")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payments"

    def __str__(self):
        return f"Payment #{self.pk} — {self.mpesa_ref or 'No ref'}"

# ============================
# RATING MODEL
# ============================

class Rating(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="rating")
    rater = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="given_ratings")
    ratee = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="received_ratings")
    stars = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ratings"

    def __str__(self):
        return f"Rating {self.stars}★ on Booking #{self.booking_id}"

# ============================
# DISPUTE MODEL
# ============================

class Dispute(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("under_review", "Under Review"),
        ("resolved", "Resolved"),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="disputes")
    raised_by = models.ForeignKey(UserAccount, on_delete=models.CASCADE, related_name="raised_disputes")
    reason = models.CharField(max_length=100)
    description = models.TextField()
    evidence_url = models.URLField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    resolution = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        UserAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_disputes"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "disputes"

    def __str__(self):
        return f"Dispute #{self.pk} on Booking #{self.booking_id}"