"""
Configuration management for the Weather Bot.
Bot config (API keys, timezone, polling, etc.) is loaded from TOML stored in the DB.
BOT_TOKEN remains in .env. Other params: TOML in DB, fallback from .env when seeding.
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Any
from dotenv import load_dotenv
import pytz

load_dotenv()

DEFAULT_DATABASE_PATH = "database/weather_bot.db"


def _admin_ids_from_value(v: Any) -> List[int]:
    if isinstance(v, list):
        return [int(x) for x in v if str(x).strip().isdigit()]
    if isinstance(v, str) and v:
        return [int(uid.strip()) for uid in v.split(",") if uid.strip().isdigit()]
    return []


class Config:
    """
    Application configuration.
    Most keys are loaded from TOML in DB at startup (set_runtime_config).
    BOT_TOKEN from .env only. Other params: TOML in DB, fallback from .env when seeding.
    """
    
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OPENWEATHER_API_KEY: str = os.getenv("OPENWEATHER_API_KEY", "")
    VISUALCROSSING_API_KEY: str = os.getenv("VISUALCROSSING_API_KEY", "")
    TIMEZONE: str = os.getenv("TIMEZONE", "UTC")
    POLLING_INTERVAL_MINUTES: int = int(os.getenv("POLLING_INTERVAL_MINUTES", "30"))
    API_REQUEST_DELAY_SECONDS: float = float(os.getenv("API_REQUEST_DELAY_SECONDS", "2"))
    LOG_LEVEL: str = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() in ("true", "1", "yes")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH)
    ADMIN_USER_IDS: List[int] = _admin_ids_from_value(os.getenv("ADMIN_USER_IDS", ""))
    
    @classmethod
    def set_runtime_config(cls, config: dict) -> None:
        """Overwrite config from DB TOML. Called at startup after loading TOML."""
        if "openweather_api_key" in config:
            cls.OPENWEATHER_API_KEY = str(config["openweather_api_key"] or "")
        if "visualcrossing_api_key" in config:
            cls.VISUALCROSSING_API_KEY = str(config["visualcrossing_api_key"] or "")
        if "timezone" in config:
            cls.TIMEZONE = str(config["timezone"] or "UTC")
        if "polling_interval_minutes" in config:
            cls.POLLING_INTERVAL_MINUTES = int(config["polling_interval_minutes"] or 30)
        if "api_request_delay_seconds" in config:
            cls.API_REQUEST_DELAY_SECONDS = float(config["api_request_delay_seconds"] or 2)
        if "log_level" in config:
            cls.LOG_LEVEL = (str(config["log_level"] or "INFO")).upper()
        if "debug_mode" in config:
            v = config["debug_mode"]
            cls.DEBUG_MODE = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")
        if "database_path" in config:
            cls.DATABASE_PATH = str(config["database_path"] or DEFAULT_DATABASE_PATH)
        if "admin_user_ids" in config:
            cls.ADMIN_USER_IDS = _admin_ids_from_value(config["admin_user_ids"])
    
    @classmethod
    def get_timezone(cls) -> pytz.timezone:
        """Get the configured timezone object."""
        try:
            return pytz.timezone(cls.TIMEZONE)
        except pytz.exceptions.UnknownTimeZoneError:
            logging.warning(f"Unknown timezone '{cls.TIMEZONE}', using UTC")
            return pytz.UTC
    
    @classmethod
    def validate(cls) -> List[str]:
        """
        Validate configuration and return list of errors.
        Returns empty list if configuration is valid.
        """
        errors = []
        
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is required")
        
        if not cls.OPENWEATHER_API_KEY:
            errors.append("OPENWEATHER_API_KEY is required")
        
        if not cls.VISUALCROSSING_API_KEY:
            errors.append("VISUALCROSSING_API_KEY is required")
        
        if cls.POLLING_INTERVAL_MINUTES < 1:
            errors.append("POLLING_INTERVAL_MINUTES must be at least 1")
        
        if cls.API_REQUEST_DELAY_SECONDS < 0:
            errors.append("API_REQUEST_DELAY_SECONDS cannot be negative")
        
        return errors
    
    @classmethod
    def setup_logging(cls) -> None:
        """Configure logging based on settings."""
        log_level = getattr(logging, cls.LOG_LEVEL, logging.INFO)
        
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[logging.StreamHandler()]
        )
        
        # Reduce noise from external libraries
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("telegram").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
    
    @classmethod
    def ensure_data_dir(cls) -> None:
        """Ensure the data directory exists."""
        db_path = Path(cls.DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)


# Default weather condition thresholds (can be overridden per location)
class DefaultWeatherLimits:
    """Default weather limits for flying conditions."""
    
    # Temperature limits in Celsius
    TEMP_MIN: float = 5.0
    TEMP_MAX: float = 35.0
    
    # Maximum humidity percentage
    HUMIDITY_MAX: float = 85.0
    
    # Maximum wind speed in m/s
    WIND_SPEED_MAX: float = 8.0
    
    # Allowed wind directions (degrees, with tolerance)
    # Example: [0, 90, 180, 270] for N, E, S, W
    WIND_DIRECTIONS: List[int] = []  # Empty means all directions allowed
    WIND_DIRECTION_TOLERANCE: int = 45  # Degrees tolerance for direction
    
    # Minimum dew point spread (temp - dew_point)
    DEW_POINT_SPREAD_MIN: float = 2.0
    
    # Required continuous hours of good conditions
    REQUIRED_CONDITIONS_DURATION_HOURS: int = 4
    
    # Maximum precipitation probability percentage
    PRECIPITATION_PROBABILITY_MAX: float = 20.0
    
    # Maximum cloud cover percentage
    CLOUD_COVER_MAX: float = 80.0
