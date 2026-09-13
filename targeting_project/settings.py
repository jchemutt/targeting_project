"""
Django settings for targeting_project.

Secrets and environment-specific values are loaded from ``data.json`` in the
project root (kept out of version control). See README.md for its format.
"""

import json
import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment-specific configuration.
with open(os.path.join(str(BASE_DIR), 'data.json')) as f:
    data = json.load(f)


# --- Security ---------------------------------------------------------------
# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = data['SECRET_KEY']

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = data.get('DEBUG', False)

ALLOWED_HOSTS = data.get('ALLOWED_HOSTS', [])
CSRF_TRUSTED_ORIGINS = data.get('CSRF_TRUSTED_ORIGINS', [])

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB

# Send the page origin as the referrer on cross-origin requests.
# Django defaults this to "same-origin", which strips the Referer header
# entirely cross-origin — that breaks embedded YouTube players
# (Error 153: embedder.identity.missing.referrer). This value is the modern
# browser default and still safe: it never leaks the full path cross-origin.
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'


# --- Application definition --------------------------------------------------
INSTALLED_APPS = [
    'targeting_app',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'targeting_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'targeting_project.wsgi.application'


# --- Database ----------------------------------------------------------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': data['postgres_db'],
        'USER': data['postgres_user'],
        'PASSWORD': data['postgres_pass'],
        'HOST': data['postgres_host'],
        'PORT': data.get('postgres_port', '5432'),
        'TEST': {
            'NAME': 'mytestdatabase',
        },
    }
}


# --- Password validation -----------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# --- Internationalization ----------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# --- Static & media files ----------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


# --- Sessions ----------------------------------------------------------------
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Email & admin alerts -----------------------------------------------------
# ADMINS in data.json looks like: [["Jane Doe", "jane@example.org"]].
# Defaults to the console backend (prints emails to stdout) so nothing
# crashes or silently tries to send real mail when it isn't configured yet;
# set EMAIL_BACKEND/EMAIL_HOST etc. in data.json for a real deployment.
ADMINS = [tuple(a) for a in data.get('ADMINS', [])]
MANAGERS = ADMINS
EMAIL_BACKEND = data.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = data.get('EMAIL_HOST', '')
EMAIL_PORT = data.get('EMAIL_PORT', 587)
EMAIL_HOST_USER = data.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = data.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = data.get('EMAIL_USE_TLS', True)
DEFAULT_FROM_EMAIL = data.get('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
SERVER_EMAIL = data.get('SERVER_EMAIL', DEFAULT_FROM_EMAIL)


# --- Output retention & storage monitoring ------------------------------------
# Analysis outputs under media/output/<run>/ are deleted automatically after
# this many days by the `cleanup_old_outputs` management command (meant to
# run daily via cron — see that command's docstring for the crontab line).
# Also read by _js_config() so the "results are kept for N days" notice
# shown to users always matches the real configured value.
OUTPUT_RETENTION_DAYS = int(data.get('OUTPUT_RETENTION_DAYS', 14))
# If free space on the media volume drops below this percentage, or the
# output folder alone exceeds it as a share of total disk size,
# cleanup_old_outputs emails ADMINS a warning — so storage running low can
# be caught and acted on between daily cleanup runs, not discovered only
# once the disk is actually full.
STORAGE_ALERT_FREE_PERCENT_THRESHOLD = float(data.get('STORAGE_ALERT_FREE_PERCENT_THRESHOLD', 10))


# --- Logging -----------------------------------------------------------------
# Replaces the ad-hoc print() debugging that used to live in the views.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'targeting_app': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}
