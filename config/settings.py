import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get('DEBUG', '1') == '1'
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError('Set SECRET_KEY in production')
    key_file = BASE_DIR / '.local-secret'
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(64), encoding='utf-8')
    SECRET_KEY = key_file.read_text(encoding='utf-8')
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
CSRF_TRUSTED_ORIGINS = [s for s in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if s]
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'learning']
FORM_RENDERER = 'django.forms.renderers.TemplatesSetting'
INSTALLED_APPS += ['django.forms']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ.get('DATABASE_PATH', str(BASE_DIR / 'db.sqlite3')), 'OPTIONS': {'timeout': 30}}}
if os.environ.get('DB_ENGINE') == 'postgresql':
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ.get('DB_NAME', 'infocode'), 'USER': os.environ.get('DB_USER', 'infocode'), 'PASSWORD': os.environ.get('DB_PASSWORD', ''), 'HOST': os.environ.get('DB_HOST', 'db'), 'PORT': os.environ.get('DB_PORT', '5432'), 'CONN_MAX_AGE': 60, 'CONN_HEALTH_CHECKS': True}}
else:
    DATABASES['default']['OPTIONS']['transaction_mode'] = 'IMMEDIATE'
# Any non-empty password is accepted: pupils choose simple ones for convenience (owner's decision).
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'uploads'
MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', str(MEDIA_ROOT)))
MEDIA_URL = '/protected-media/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
# Shown on the public contacts page; empty values are hidden.
SITE_OWNER_NAME = os.environ.get('SITE_OWNER_NAME', '')
SITE_CONTACT_EMAIL = os.environ.get('SITE_CONTACT_EMAIL', '')
SITE_CONTACT_TELEGRAM = os.environ.get('SITE_CONTACT_TELEGRAM', '')
# Google Search Console ownership check (HTML tag method): the tag's content value.
GOOGLE_SITE_VERIFICATION = os.environ.get('GOOGLE_SITE_VERIFICATION', '')
# Seasonal decoration: 'winter' adds frost and optional snowfall; empty turns it off.
KR_API_TOKEN = os.environ.get('KR_API_TOKEN', '')  # shared secret of the separate test platform (learning/kr_api.py)
KR_INTERNAL_URL = os.environ.get('KR_INTERNAL_URL', 'http://kr:8000')  # the test platform inside the Docker network
KR_PUBLIC_URL = os.environ.get('KR_PUBLIC_URL', '/kr')  # where teachers and pupils open it
EMBED_MODEL_DIR = Path(os.environ.get('EMBED_MODEL_DIR', str(BASE_DIR / 'models' / 'e5-small')))  # learning/embeddings.py
SITE_SEASON = os.environ.get('SITE_SEASON', 'auto')  # learning/seasons.py
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
LOGIN_RATE_LIMIT = int(os.environ.get('LOGIN_RATE_LIMIT', '12'))
LOGIN_RATE_WINDOW = 900
MIDDLEWARE.insert(0, 'learning.middleware.RequestLogMiddleware')
MIDDLEWARE.insert(MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1, 'learning.middleware.LoginThrottleMiddleware')
MIDDLEWARE.insert(MIDDLEWARE.index('learning.middleware.LoginThrottleMiddleware') + 1, 'learning.middleware.TemporaryPasswordMiddleware')
TEMPLATES[0]['OPTIONS']['context_processors'].append('learning.context_processors.platform_context')
TEMPLATES[0]['OPTIONS']['context_processors'].append('learning.context_processors.season_context')
TEMPLATES[0]['OPTIONS']['context_processors'].append('learning.subject_views.subject_context')
MIDDLEWARE.insert(MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1, 'learning.subjects.SubjectMiddleware')
LOG_DIR = Path(os.environ.get('LOG_DIR', str(BASE_DIR / 'logs')))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'formatters': {'plain': {'format': '{asctime} {levelname} {message}', 'style': '{'}}, 'handlers': {'file': {'class': 'logging.handlers.RotatingFileHandler', 'filename': str(LOG_DIR / 'errors.log'), 'maxBytes': 2_000_000, 'backupCount': 3, 'formatter': 'plain', 'encoding': 'utf-8'}}, 'loggers': {'platform': {'handlers': ['file'], 'level': 'WARNING', 'propagate': False}}}
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    # Only the site's own name: a subdomain served elsewhere (www parked at the registrar) must not be locked
    # behind HSTS with a certificate that is not ours — browsers then refuse it without a way through.
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    # Preloading a new domain is a separate, deliberate deployment decision.
    # Keep all other deployment checks enabled; HTTPS/HSTS still apply normally.
    SECURE_HSTS_PRELOAD = False
    SILENCED_SYSTEM_CHECKS = ['security.W021', 'security.W005']
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
