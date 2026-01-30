"""
Configuration management for the Weather Bot.
Loads settings from environment variables and provides defaults.
"""

import os
import logging
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
import pytz

# Load environment variables from .env file
load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""
    
    # Telegram Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # Weather API Keys
    OPENWEATHER_API_KEY: str = os.getenv("OPENWEATHER_API_KEY", "")
    VISUALCROSSING_API_KEY: str = os.getenv("VISUALCROSSING_API_KEY", "")
    
    # Timing Settings
    TIMEZONE: str = os.getenv("TIMEZONE", "UTC")
    POLLING_INTERVAL_MINUTES: int = int(os.getenv("POLLING_INTERVAL_MINUTES", "30"))
    API_REQUEST_DELAY_SECONDS: float = float(os.getenv("API_REQUEST_DELAY_SECONDS", "2"))
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/weather_bot.db")
    
    # Admin user IDs (comma-separated in env)
    _admin_ids_str: str = os.getenv("ADMIN_USER_IDS", "")
    ADMIN_USER_IDS: List[int] = [
        int(uid.strip()) 
        for uid in _admin_ids_str.split(",") 
        if uid.strip().isdigit()
    ]
    
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
