"""
"""
import os
from datetime import timedelta
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-41$^@khxp$pfnqxc1g9=+2@s9dev_*vas_7qyda+-j7f9k61v4'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["*"]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'mwalimuApp',
    'sendgrid_backend',
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = 'mwalimuProject.urls'

REACT_BUILD_DIR = BASE_DIR / 'frontend' / 'dist'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [REACT_BUILD_DIR],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mwalimuProject.wsgi.application'

# Rest framework

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

EMAIL_BACKEND = 'sendgrid_backend.SendgridBackend'
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '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')
SENDGRID_SANDBOX_MODE_IN_DEBUG = False
DEFAULT_FROM_EMAIL = 'Mwalimu Pool <noreply@mwalimupool.co.ke>'

# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

# Media URL and Root

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_ROOT = BASE_DIR / 'staticfiles'

STATIC_URL = '/static/'

STATICFILES_DIRS = [
    REACT_BUILD_DIR / 'assets',
]

CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8080")

AUTH_USER_MODEL = 'mwalimuApp.UserAccount'

# SasaPay Credentials (https://docs.sasapay.app)
# Used to power wallet C2B (deposits) and B2C (withdrawals/payouts).
SASAPAY_ENV = "production"  # "sandbox" or "production"
SASAPAY_CLIENT_ID = "OfB2qOtCYJwStJVz6e75jZ5SFfoKil2lmrAeB8MX"
SASAPAY_CLIENT_SECRET = "5i5JXDOHHRotx9zrfyiJ2e3UggufLi07En41bThuzQCECDkV0rBJQC5wPMl07Z9rrYBisrCpkIfR09IPNDeFqtUzWYtW8nhXuhT4OPWL3B1c9j4TBRN5RqF66idfYsYB"
SASAPAY_MERCHANT_CODE = "1468164"
SASAPAY_C2B_CALLBACK_URL = "https://mwalimupool.co.ke/api/sasapay/c2b/callback/"
SASAPAY_B2C_CALLBACK_URL = "https://mwalimupool.co.ke/api/sasapay/b2c/callback/"

# Wallet config
PLATFORM_FEE_PERCENT = float(os.environ.get("PLATFORM_FEE_PERCENT", "10"))  # %
MIN_WITHDRAWAL_KES = float(os.environ.get("MIN_WITHDRAWAL_KES", "100"))
