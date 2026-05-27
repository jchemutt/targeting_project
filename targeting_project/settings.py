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
