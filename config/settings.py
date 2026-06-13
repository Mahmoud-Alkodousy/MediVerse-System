"""
MediVerse - Centralized Configuration
All settings in one place. Uses environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class DatabaseConfig:
    DRIVER = os.getenv("DATABASE_DRIVER", "ODBC Driver 17 for SQL Server")
    SERVER = os.getenv("DATABASE_SERVER", "localhost")
    NAME = os.getenv("DATABASE_NAME", "MediVerse_System")
    USER = os.getenv("DATABASE_USER", "")
    PASSWORD = os.getenv("DATABASE_PASSWORD", "")
    POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "10"))
    MAX_OVERFLOW = int(os.getenv("DATABASE_MAX_OVERFLOW", "20"))
    TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "30"))
    POOL_RECYCLE = int(os.getenv("DATABASE_POOL_RECYCLE", "3600"))

    @classmethod
    def get_pyodbc_connection_string(cls) -> str:
        if cls.USER:
            return (
                f"DRIVER={{{cls.DRIVER}}};"
                f"SERVER={cls.SERVER};"
                f"DATABASE={cls.NAME};"
                f"UID={cls.USER};"
                f"PWD={cls.PASSWORD};"
                "Encrypt=yes;"
            )
        return (
            f"DRIVER={{{cls.DRIVER}}};"
            f"SERVER={cls.SERVER};"
            f"DATABASE={cls.NAME};"
            "Trusted_Connection=yes;"
            "Encrypt=no;"
        )

    @classmethod
    def get_sqlalchemy_url(cls) -> str:
        user_pass = ""
        if cls.USER and cls.PASSWORD:
            user_pass = f"{cls.USER}:{cls.PASSWORD}@"
        driver = cls.DRIVER.replace(" ", "+")
        return f"mssql+pyodbc://{user_pass}{cls.SERVER}/{cls.NAME}?driver={driver}"


class FaceRecognitionConfig:
    SIMILARITY_THRESHOLD = float(os.getenv("FACE_SIMILARITY_THRESHOLD", "0.65"))
    MODEL_PATH = os.getenv("FACE_MODEL_PATH", "models/auraface")
    MIN_FACE_SIZE = int(os.getenv("MIN_FACE_SIZE", "80"))
    MAX_FACE_ANGLE = int(os.getenv("MAX_FACE_ANGLE", "30"))
    QUALITY_THRESHOLD = float(os.getenv("FACE_QUALITY_THRESHOLD", "0.5"))


class ImageConfig:
    MAX_SIZE_MB = int(os.getenv("MAX_IMAGE_SIZE_MB", "10"))
    MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
    ALLOWED_FORMATS = [".jpg", ".jpeg", ".png", ".bmp", ".webp"]
    TEMP_UPLOAD_DIR = os.getenv("TEMP_UPLOAD_DIR", "temp_uploads")


class JWTConfig:
    SECRET_KEY = os.getenv("JWT_SECRET_KEY", "mediverse-dev-secret-change-in-production")
    ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
    REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))


class ChatbotConfig:
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
    EMBED_MODEL = os.getenv("EMBED_MODEL_NAME", "paraphrase-multilingual-MiniLM-L12-v2")
    RAG_THRESHOLD = float(os.getenv("RAG_THRESHOLD", "0.55"))
    RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))

    MODEL_DISPLAY_NAMES = {
        "openai/gpt-4o": "GPT-4o 🧠",
        "openai/gpt-4o-mini": "GPT-4o Mini 🚀",
        "google/gemini-pro-1.5": "Gemini Pro 1.5 💎",
        "anthropic/claude-3-haiku": "Claude 3 Haiku ⚡",
        "anthropic/claude-3.5-sonnet": "Claude 3.5 Sonnet 🎭",
        "meta-llama/llama-3.1-70b-instruct": "Llama 3.1 70B 🦙",
        "mistralai/mixtral-8x7b-instruct": "Mixtral 8x7B 🌟",
    }

    SPECIALTIES = {
        "General Practitioner": "طب أسرة / ممارس عام",
        "Family Medicine": "طب الأسرة",
        "Neurology": "طب المخ والأعصاب",
        "Cardiology": "أمراض القلب",
        "Orthopedics": "طب العظام",
        "Pulmonology": "أمراض الصدر",
        "Pediatrics": "طب الأطفال",
        "Emergency Medicine": "طب الطوارئ",
        "Ophthalmology": "طب العيون",
        "Dermatology": "الأمراض الجلدية",
        "Internal Medicine": "الباطنة",
        "Psychiatry": "الطب النفسي",
    }


class RedisConfig:
    HOST = os.getenv("REDIS_HOST", "localhost")
    PORT = int(os.getenv("REDIS_PORT", "6379"))


class CORSConfig:
    ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ]


class RateLimitConfig:
    CALLS = int(os.getenv("RATE_LIMIT_CALLS", "100"))
    PERIOD = int(os.getenv("RATE_LIMIT_PERIOD", "60"))


class LogConfig:
    LEVEL = os.getenv("LOG_LEVEL", "INFO")
    FILE = os.getenv("LOG_FILE", "mediverse_api.log")
    MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "10485760"))
    BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    LOG_REQUESTS = os.getenv("LOG_REQUESTS", "true").lower() == "true"


class AppConfig:
    """Top-level config aggregating all sub-configs."""
    ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
    APP_TITLE = "MediVerse Hospital System API"
    APP_VERSION = "4.0.0"
    APP_DESCRIPTION = "Smart Hospital Management System - Face Recognition + AI Consultation + Queue Management"

    db = DatabaseConfig
    face = FaceRecognitionConfig
    image = ImageConfig
    jwt = JWTConfig
    chatbot = ChatbotConfig
    redis = RedisConfig
    cors = CORSConfig
    rate_limit = RateLimitConfig
    log = LogConfig

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENVIRONMENT.lower() in ("prod", "production")

    @classmethod
    def is_development(cls) -> bool:
        return cls.ENVIRONMENT.lower() in ("dev", "development")


settings = AppConfig()
