"""
Database models for the Weather Bot.
These dataclasses represent the structure of data stored in SQLite.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import json


@dataclass
class Location:
    """
    A monitored location with weather conditions rules.
    
    Attributes:
        id: Unique identifier (auto-generated)
        chat_id: Telegram chat/channel ID this location belongs to
        name: Human-readable location name
            Example: "Юца", "Чегем", "Mount Clemens"
        latitude: Geographic latitude
            Example: 43.9234
        longitude: Geographic longitude  
            Example: 42.7345
        
        # Time window settings
        time_window_start: Start hour for checking (0-23)
            Example: 8 (means 08:00)
        time_window_end: End hour for checking (0-23)
            Example: 18 (means 18:00)
        
        # Temperature settings (Celsius)
        temp_min: Minimum acceptable temperature
            Example: 5.0
        
        # Humidity settings (percentage)
        humidity_max: Maximum acceptable humidity
            Example: 85.0
        
        # Wind settings
        wind_speed_max: Maximum wind speed in m/s
            Example: 8.0
        wind_directions: Allowed wind directions as JSON array of degrees
            Example: "[0, 45, 90, 315]" for N, NE, E, NW
            Empty array "[]" means all directions allowed
        wind_direction_tolerance: Tolerance in degrees for wind direction
            Example: 45
        
        # Dew point settings
        dew_point_spread_min: Minimum difference between temp and dew point
            Example: 2.0 (prevents fog/condensation)
        
        # Duration settings
        required_conditions_duration_hours: How many continuous hours of good conditions needed
            Example: 4
        
        # Additional settings
        precipitation_probability_max: Maximum precipitation chance (%)
            Example: 20.0
        cloud_cover_max: Maximum cloud cover (%)
            Example: 80.0
        
        # Metadata
        is_active: Whether this location is actively monitored
        created_at: When this location was created
        updated_at: When this location was last updated
    """
    chat_id: int
    name: str
    latitude: float
    longitude: float
    
    # Time window (hours, 0-23)
    time_window_start: int = 8
    time_window_end: int = 18
    
    # Temperature (Celsius)
    temp_min: float = 5.0
    
    # Humidity (%)
    humidity_max: float = 85.0
    
    # Wind
    wind_speed_max: float = 8.0
    wind_directions: str = "[]"  # JSON array of allowed directions in degrees
    wind_direction_tolerance: int = 45
    
    # Dew point
    dew_point_spread_min: float = 2.0
    
    # Duration
    required_conditions_duration_hours: int = 4
    
    # Additional
    precipitation_probability_max: float = 20.0
    cloud_cover_max: float = 80.0
    
    # Metadata
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: Optional[int] = None
    
    def get_wind_directions_list(self) -> List[int]:
        """Parse wind directions from JSON string to list."""
        try:
            return json.loads(self.wind_directions)
        except json.JSONDecodeError:
            return []
    
    def set_wind_directions_list(self, directions: List[int]) -> None:
        """Set wind directions from list to JSON string."""
        self.wind_directions = json.dumps(directions)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "time_window_start": self.time_window_start,
            "time_window_end": self.time_window_end,
            "temp_min": self.temp_min,
            "humidity_max": self.humidity_max,
            "wind_speed_max": self.wind_speed_max,
            "wind_directions": self.wind_directions,
            "wind_direction_tolerance": self.wind_direction_tolerance,
            "dew_point_spread_min": self.dew_point_spread_min,
            "required_conditions_duration_hours": self.required_conditions_duration_hours,
            "precipitation_probability_max": self.precipitation_probability_max,
            "cloud_cover_max": self.cloud_cover_max,
            "is_active": self.is_active,
        }


@dataclass
class ChatSettings:
    """
    Settings for a specific chat/channel.
    
    Attributes:
        chat_id: Telegram chat/channel ID
        chat_type: Type of chat (private, group, supergroup, channel)
        chat_title: Title of the group/channel or username for private chats
        
        # Message templates (MarkdownV2 format)
        flyable_template: Template for "weather is flyable" notifications
        not_flyable_template: Template for "weather is not flyable" notifications
        
        # Notification settings
        notifications_enabled: Whether to send automatic notifications
        
        # Metadata
        created_at: When this chat was first registered
        updated_at: When settings were last updated
    """
    chat_id: int
    chat_type: str = "private"
    chat_title: Optional[str] = None
    
    # Message templates (MarkdownV2 format)
    flyable_template: str = """✅🪂 *ЛЁТНАЯ ПОГОДА\\!*

📍 *Локация:* {location_name}
📅 *Дата:* {date}
⏰ *Лётное окно:* {flyable_window}

*Условия:*
🌡 Температура: {temp_range}
💨 Ветер: {wind_info}
💧 Влажность: {humidity}%
🌤 Облачность: {cloud_cover}%

_Данные подтверждены двумя источниками_
_Обновлено: {updated_at}_"""

    not_flyable_template: str = """❌🌧️ *СТАЛО НЕ ЛЁТНО*

📍 *Локация:* {location_name}
📅 *Дата:* {date}

*Причины отмены:*
{rejection_reasons}

*Текущие условия:*
🌡 Температура: {temp}°C
💨 Ветер: {wind_speed} м/с, {wind_direction}°
💧 Влажность: {humidity}%
🌤 Облачность: {cloud_cover}%

_Обновлено: {updated_at}_"""
    
    notifications_enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "chat_title": self.chat_title,
            "flyable_template": self.flyable_template,
            "not_flyable_template": self.not_flyable_template,
            "notifications_enabled": self.notifications_enabled,
        }


@dataclass
class FlyableWindow:
    """
    A flyable weather window in the forecast.
    
    Represents a continuous period where all weather conditions
    are suitable for flying.
    """
    location_id: int
    forecast_id: int  # Reference to the forecast that predicted this window
    date: str  # YYYY-MM-DD format
    start_hour: int  # 0-23
    end_hour: int  # 0-23
    duration_hours: int
    
    # Which source(s): "both", "openweather", "visualcrossing"
    source: str = "both"
    
    # Weather summary for this window
    avg_temp: Optional[float] = None
    avg_wind_speed: Optional[float] = None
    max_wind_speed: Optional[float] = None
    avg_humidity: Optional[float] = None
    max_precipitation_prob: Optional[float] = None
    
    # Notification tracking
    notified: bool = False
    notified_at: Optional[datetime] = None
    cancelled: bool = False  # True if window was cancelled in later forecast
    cancelled_at: Optional[datetime] = None
    
    # Metadata
    created_at: Optional[datetime] = None
    id: Optional[int] = None
    
    def to_display_string(self) -> str:
        """Format window for display."""
        return f"{self.date} {self.start_hour:02d}:00-{self.end_hour:02d}:00 ({self.duration_hours}ч)"


@dataclass 
class WeatherForecast:
    """
    Stored weather forecast for a location.
    
    Contains the full forecast data and list of detected flyable windows.
    """
    location_id: int
    check_time: datetime
    
    # Forecast horizon
    forecast_start: datetime
    forecast_end: datetime
    
    # Raw API data
    openweather_data: str = "{}"
    visualcrossing_data: str = "{}"
    
    # Analysis summary
    total_flyable_windows: int = 0
    flyable_windows_json: str = "[]"  # JSON array of window info
    
    # Metadata
    created_at: Optional[datetime] = None
    id: Optional[int] = None
    
    def get_flyable_windows(self) -> List[dict]:
        """Parse flyable windows from JSON."""
        try:
            return json.loads(self.flyable_windows_json)
        except json.JSONDecodeError:
            return []
    
    def set_flyable_windows(self, windows: List[dict]) -> None:
        """Set flyable windows as JSON."""
        self.flyable_windows_json = json.dumps(windows, ensure_ascii=False, default=str)


@dataclass
class WeatherStatus:
    """
    Current weather status for a location.
    Tracks whether conditions are flyable and change history.
    
    Attributes:
        location_id: Reference to the Location
        date: Date this status is for (legacy, kept for compatibility)
        is_flyable: Whether any flyable windows exist in forecast
        
        # Current forecast state
        active_windows_json: JSON array of currently predicted flyable windows
        last_forecast_id: ID of the last forecast used
        
        # For tracking status changes
        consecutive_not_flyable_checks: Number of consecutive checks with no flyable windows
        last_notification_type: Last notification sent ('flyable', 'not_flyable', or None)
        last_notification_at: When the last notification was sent
        
        # Metadata
        created_at: When this status was created
        updated_at: When this status was last updated
    """
    location_id: int
    date: str  # YYYY-MM-DD format (date of last check)
    is_flyable: bool = False
    
    # Current forecast windows
    active_windows_json: str = "[]"  # JSON array of {date, start_hour, end_hour, duration}
    last_forecast_id: Optional[int] = None
    
    # Legacy fields for compatibility
    flyable_window_start: Optional[str] = None  # HH:MM format
    flyable_window_end: Optional[str] = None  # HH:MM format
    
    # Status change tracking
    consecutive_not_flyable_checks: int = 0
    last_notification_type: Optional[str] = None
    last_notification_at: Optional[datetime] = None
    
    # Metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: Optional[int] = None
    
    def get_active_windows(self) -> List[dict]:
        """Parse active windows from JSON."""
        try:
            return json.loads(self.active_windows_json)
        except json.JSONDecodeError:
            return []
    
    def set_active_windows(self, windows: List[dict]) -> None:
        """Set active windows as JSON."""
        self.active_windows_json = json.dumps(windows, ensure_ascii=False, default=str)


@dataclass
class WeatherCheck:
    """
    Record of a weather check for auditing and debugging.
    
    Attributes:
        location_id: Reference to the Location
        check_time: When the check was performed
        
        # Raw data from APIs
        openweather_data: JSON string of OpenWeather response
        visualcrossing_data: JSON string of VisualCrossing response
        
        # Analysis results
        is_flyable: Whether conditions were determined to be flyable
        rejection_reasons: JSON array of reasons why conditions weren't flyable (if any)
        flyable_hours: JSON array of hours that meet flying conditions
        
        # Metadata
        created_at: When this record was created
    """
    location_id: int
    check_time: datetime
    
    # Raw API data
    openweather_data: str = "{}"
    visualcrossing_data: str = "{}"
    
    # Analysis results
    is_flyable: bool = False
    rejection_reasons: str = "[]"  # JSON array
    flyable_hours: str = "[]"  # JSON array of hour strings
    
    # Metadata
    created_at: Optional[datetime] = None
    id: Optional[int] = None
    
    def get_rejection_reasons_list(self) -> List[str]:
        """Parse rejection reasons from JSON string."""
        try:
            return json.loads(self.rejection_reasons)
        except json.JSONDecodeError:
            return []
    
    def set_rejection_reasons_list(self, reasons: List[str]) -> None:
        """Set rejection reasons from list."""
        self.rejection_reasons = json.dumps(reasons, ensure_ascii=False)


@dataclass
class AdminUser:
    """
    Admin user for a specific chat.
    
    Attributes:
        chat_id: Telegram chat/channel ID
        user_id: Telegram user ID of the admin
        username: Telegram username (optional)
        added_at: When this admin was added
    """
    chat_id: int
    user_id: int
    username: Optional[str] = None
    added_at: Optional[datetime] = None
    id: Optional[int] = None
