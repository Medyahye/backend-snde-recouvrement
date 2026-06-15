"""
Configuration Django pour le projet SNDE.
Les valeurs sensibles sont lues depuis l'environnement (.env).
"""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Sécurité ---
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend"
).split(",")
if DEBUG:
    # IPs de développement (Mohamed = .188, Leila = .113).
    # En mode dev, on accepte aussi tout 192.168.* via le wildcard
    # pour faciliter le test mobile depuis n'importe quel téléphone.
    ALLOWED_HOSTS += ["192.168.0.188", "192.168.100.113", "*"]

# --- Applications ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Tiers
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    # Apps internes
    "apps.users",
    "apps.imports",
    "apps.clients",
    "apps.zones",
    "apps.scoring",
    "apps.recouvrement",
    "apps.terrain",
    "apps.api",
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

ROOT_URLCONF = "snde_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "snde_backend.wsgi.application"
ASGI_APPLICATION = "snde_backend.asgi.application"

# --- Base de données ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "snde"),
        "USER": os.environ.get("POSTGRES_USER", "snde"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "snde"),
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18N ---
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Nouakchott"
USE_I18N = True
USE_TZ = True

# --- Static ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF + JWT ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
}

# --- CORS (front local) ---
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ALLOW_CREDENTIALS = True

# --- Celery ---
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/1")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/2")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 min max par fichier FAB
CELERY_TIMEZONE = "Africa/Nouakchott"

# --- Celery Beat — tâches planifiées ---
# Lance la synchro S3 → MinIO tous les jours à 02:00 du matin (heure Nouakchott).
# SNDE dépose typiquement le FAB du jour en soirée, on ingère avant ouverture
# des bureaux pour que les agents aient les données fraîches dès 08:00.
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "sync-s3-daily": {
        "task": "scoring.sync_s3_daily",
        "schedule": crontab(hour=2, minute=0),
        "options": {"expires": 60 * 60 * 4},  # tâche expire si pas exécutée en 4h
    },
}

# --- MinIO ---
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET_FAB = os.environ.get("MINIO_BUCKET_FAB", "fab-imports")
MINIO_BUCKET_PHOTOS = os.environ.get("MINIO_BUCKET_PHOTOS", "meter-photos")
MINIO_USE_SSL = os.environ.get("MINIO_USE_SSL", "0") == "1"
# Endpoint MinIO accessible depuis le téléphone (LAN). Si non défini, on
# retombe sur l'endpoint interne — fonctionnera en dev sur Mac uniquement.
# En prod, configurer un host public accessible (ex: minio.snde.local:9000).
MINIO_PUBLIC_ENDPOINT = (
    os.environ.get("MINIO_PUBLIC_ENDPOINT", "") or MINIO_ENDPOINT
)

# --- AWS S3 (source externe des FABs envoyés par SNDE) ---
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "")
AWS_S3_REGION = os.environ.get("AWS_S3_REGION", "eu-west-1")
AWS_S3_PREFIX = os.environ.get("AWS_S3_PREFIX", "")
# Seuil sous lequel un FAB est considéré "vide"
FAB_EMPTY_MIN_VALID_LINES = int(os.environ.get("FAB_EMPTY_MIN_VALID_LINES", 100))

# --- Constantes métier (cf. Note Explicative §5) ---
# Poids du score (somme = 1.0)
SCORING_WEIGHTS = {
    "MONTANT": 0.40,
    "ANCIENNETE": 0.25,
    "HISTORIQUE": 0.20,
    "ARRIERES": 0.15,
}
# Seuil de plafonnement (jours) pour Anciennete_norm et Historique_norm
SCORING_THRESHOLD_DAYS = 180
# Activités considérées comme Domestique (coef 1.00). Reste = Entreprise (coef 1.20).
DOMESTIQUE_ACTIVITIES = {"TOUS CLIENTS DOMESTIQUES", "BRANCHEMENTS SOCIAUX"}
COEF_DOMESTIQUE = 1.00
COEF_ENTREPRISE = 1.20
# Quantiles de catégorisation Haute/Moyenne/Faible
PRIORITY_QUANTILE_HIGH = 0.75
PRIORITY_QUANTILE_MED = 0.50

# --- Upload FAB ---
# Taille max d'un FAB (200 Mo par défaut).
FAB_MAX_UPLOAD_SIZE = int(os.environ.get("FAB_MAX_UPLOAD_SIZE", 200 * 1024 * 1024))
# Strict : la date dans le nom de fichier doit être celle du jour (TZ Africa/Nouakchott).
# Mettre à "0" en .env pour autoriser n'importe quelle date (utile pour tester avec un FAB ancien).
FAB_REQUIRE_TODAY_DATE = os.environ.get("FAB_REQUIRE_TODAY_DATE", "1") == "1"
# Nombre de lignes échantillonnées en début de fichier pour valider la structure FAB.
FAB_CONTENT_VALIDATION_SAMPLE_LINES = 50
# Pattern de nom de fichier attendu : fabAAAAMMJJ.txt
FAB_FILENAME_REGEX = r"^fab(\d{4})(\d{2})(\d{2})\.txt$"

# --- Scoring engine ---
# "formula" (défaut) ou "ft_transformer" (modèle IA entraîné)
SCORING_ENGINE = os.environ.get("SCORING_ENGINE", "formula")
