"""
Weather analyzer for determining flyable conditions.
Combines data from multiple sources and evaluates against location-specific rules.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
import pytz

from ..database.models import Location

logger = logging.getLogger(__name__)


@dataclass
class HourlyWeather:
    """Standardized hourly weather data from any source."""
    datetime: datetime
    date_str: str  # YYYY-MM-DD
    hour: int  # 0-23
    temperature: float
    feels_like: float
    humidity: float
    dew_point: float
    wind_speed: float  # m/s
    wind_gust: float  # m/s
    wind_direction: int  # degrees
    cloud_base_m: float  # height of cloud base in meters
    fog_probability: float  # 0-100%
    precipitation_probability: float  # percentage
    visibility: float  # km
    
    # Optional fields
    precipitation_mm: float = 0.0
    snow_mm: float = 0.0
    pressure: float = 1013.0
    uv_index: float = 0.0
    weather_condition: str = ""
    weather_description: str = ""
    source: str = ""  # Which API this came from


@dataclass
class ConditionCheck:
    """Result of checking a single condition."""
    name: str
    passed: bool
    actual_value: Any
    limit_value: Any
    message: str


@dataclass
class FlyableWindowInfo:
    """Information about a single flyable window."""
    date: str  # YYYY-MM-DD
    start_hour: int  # 0-23
    end_hour: int  # 0-23
    duration_hours: int
    
    # Which source(s) report this window: "both", "openweather", "visualcrossing"
    source: str = "both"
    
    # Weather statistics for the window
    avg_temp: float = 0.0
    min_temp: float = 0.0
    max_temp: float = 0.0
    avg_wind_speed: float = 0.0
    max_wind_speed: float = 0.0
    avg_humidity: float = 0.0
    max_precipitation_prob: float = 0.0
    avg_cloud_base_m: float = 0.0
    max_fog_probability: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "date": self.date,
            "start_hour": self.start_hour,
            "end_hour": self.end_hour,
            "duration_hours": self.duration_hours,
            "source": self.source,
            "avg_temp": round(self.avg_temp, 1),
            "min_temp": round(self.min_temp, 1),
            "max_temp": round(self.max_temp, 1),
            "avg_wind_speed": round(self.avg_wind_speed, 1),
            "max_wind_speed": round(self.max_wind_speed, 1),
            "avg_humidity": round(self.avg_humidity, 1),
            "max_precipitation_prob": round(self.max_precipitation_prob, 1),
            "avg_cloud_base_m": round(self.avg_cloud_base_m, 0),
            "max_fog_probability": round(self.max_fog_probability, 0),
        }
    
    def to_display_string(self) -> str:
        """Format window for display."""
        return f"{self.date} {self.start_hour:02d}:00-{self.end_hour:02d}:00 ({self.duration_hours}ч)"


@dataclass
class FullForecastAnalysis:
    """Complete analysis result for a location across the entire forecast period."""
    location_id: int
    location_name: str
    analysis_time: datetime
    
    # Forecast horizon
    forecast_start: datetime
    forecast_end: datetime
    total_hours_analyzed: int = 0
    
    # Flyable windows found
    flyable_windows: List[FlyableWindowInfo] = field(default_factory=list)
    total_flyable_hours: int = 0
    
    # Status flags
    has_flyable_conditions: bool = False
    
    # Rejection reasons (when no flyable windows found)
    rejection_reasons: List[str] = field(default_factory=list)
    
    # Data sources used
    openweather_available: bool = False
    visualcrossing_available: bool = False
    openweather_hours: int = 0
    visualcrossing_hours: int = 0
    
    # Current conditions summary (nearest hour)
    current_temp: Optional[float] = None
    current_wind_speed: Optional[float] = None
    current_wind_direction: Optional[int] = None
    current_humidity: Optional[float] = None
    current_cloud_base_m: Optional[float] = None
    current_fog_probability: Optional[float] = None

    def get_windows_for_date(self, date_str: str) -> List[FlyableWindowInfo]:
        """Get all windows for a specific date."""
        return [w for w in self.flyable_windows if w.date == date_str]
    
    def get_next_flyable_window(self) -> Optional[FlyableWindowInfo]:
        """Get the nearest upcoming flyable window."""
        if self.flyable_windows:
            return self.flyable_windows[0]
        return None


class WeatherAnalyzer:
    """
    Analyzes weather data to determine flyable conditions across the entire forecast period.
    
    Key features:
    - Analyzes ALL days in the forecast, not just today
    - Shows flyable windows from both sources, or from either source alone
    - Finds continuous time windows where conditions are met
    - Returns multiple flyable windows if they exist
    """
    
    def __init__(self, timezone: pytz.timezone = pytz.UTC):
        """
        Initialize the weather analyzer.
        
        Args:
            timezone: Timezone for interpreting time windows
        """
        self.timezone = timezone
    
    def analyze_full_forecast(
        self,
        location: Location,
        openweather_data: Optional[Dict[str, Any]],
        visualcrossing_data: Optional[Dict[str, Any]]
    ) -> FullForecastAnalysis:
        """
        Analyze weather data across the entire forecast period.
        
        Args:
            location: Location with weather rules
            openweather_data: Parsed OpenWeather API response
            visualcrossing_data: Parsed VisualCrossing API response
        
        Returns:
            FullForecastAnalysis with all found flyable windows
        """
        now = datetime.now(self.timezone)
        
        result = FullForecastAnalysis(
            location_id=location.id,
            location_name=location.name,
            analysis_time=now,
            forecast_start=now,
            forecast_end=now,
            openweather_available=openweather_data is not None,
            visualcrossing_available=visualcrossing_data is not None
        )
        
        # Both sources must be available
        if not openweather_data or not visualcrossing_data:
            if not openweather_data:
                result.rejection_reasons.append("❌ Нет данных от OpenWeather")
            if not visualcrossing_data:
                result.rejection_reasons.append("❌ Нет данных от VisualCrossing")
            return result
        
        # Parse all hourly data from both sources
        ow_hourly = self._parse_all_hourly_data(openweather_data, "openweather")
        vc_hourly = self._parse_all_hourly_data(visualcrossing_data, "visualcrossing")
        
        result.openweather_hours = len(ow_hourly)
        result.visualcrossing_hours = len(vc_hourly)
        
        if not ow_hourly or not vc_hourly:
            result.rejection_reasons.append("❌ Нет почасовых данных в прогнозах")
            return result
        
        # Group hourly data by date
        ow_by_date = self._group_by_date(ow_hourly)
        vc_by_date = self._group_by_date(vc_hourly)
        
        # Get all dates present in both sources
        all_dates = sorted(set(ow_by_date.keys()) & set(vc_by_date.keys()))
        
        if not all_dates:
            result.rejection_reasons.append("❌ Нет совпадающих дат в прогнозах")
            return result
        
        # Update forecast horizon
        result.forecast_start = datetime.strptime(all_dates[0], "%Y-%m-%d")
        result.forecast_start = self.timezone.localize(result.forecast_start)
        result.forecast_end = datetime.strptime(all_dates[-1], "%Y-%m-%d")
        result.forecast_end = self.timezone.localize(result.forecast_end)
        
        # Analyze each date: union of flyable hours from all sources → max continuous windows
        all_flyable_windows = []
        total_hours = 0
        
        for date_str in all_dates:
            ow_day_data = ow_by_date.get(date_str, [])
            vc_day_data = vc_by_date.get(date_str, [])
            
            hours_both, hours_ow_only, hours_vc_only = self._find_flyable_hours_for_day(
                location, ow_day_data, vc_day_data
            )
            # Union: любой источник считает час лётным → включаем в окно (максимум часов)
            hours_union = sorted(set(hours_both) | set(hours_ow_only) | set(hours_vc_only))
            total_hours += len(hours_union)
            
            req = location.required_conditions_duration_hours
            combined_hourly = ow_day_data + vc_day_data
            
            # Окна по объединённым часам — максимальная непрерывная длина
            raw_windows = self._find_continuous_windows(
                date_str, hours_union, req, combined_hourly, source="both"
            )
            # Для каждого окна определяем источник по входящим часам
            for w in raw_windows:
                window_hours = set(range(w.start_hour, w.end_hour + 1))
                w.source = self._window_source(
                    window_hours, set(hours_both), set(hours_ow_only), set(hours_vc_only)
                )
            all_flyable_windows.extend(raw_windows)
        
        # Sort by date, then start_hour
        all_flyable_windows.sort(key=lambda w: (w.date, w.start_hour))
        result.flyable_windows = all_flyable_windows
        result.total_flyable_hours = total_hours
        result.total_hours_analyzed = sum(len(ow_by_date.get(d, [])) for d in all_dates)
        result.has_flyable_conditions = len(all_flyable_windows) > 0
        
        # If no flyable windows found, add rejection reasons
        if not all_flyable_windows:
            result.rejection_reasons.append(
                f"❌ Не найдено непрерывных окон минимум {location.required_conditions_duration_hours} ч."
            )
            if total_hours > 0:
                result.rejection_reasons.append(
                    f"ℹ️ Найдено {total_hours} лётных часов, но не подряд"
                )
        
        # Set current conditions from nearest hour
        self._set_current_conditions(result, ow_hourly, vc_hourly)
        
        return result
    
    def _parse_all_hourly_data(
        self, 
        data: Dict[str, Any], 
        source: str
    ) -> List[HourlyWeather]:
        """Parse ALL hourly data from API response, not filtered by date."""
        hourly_list = []
        
        for hour_data in data.get("hourly", []):
            # Parse datetime
            dt_str = hour_data.get("datetime", "")
            
            try:
                if " " in dt_str:
                    # Format: "YYYY-MM-DD HH:MM:SS"
                    dt = datetime.strptime(dt_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
                else:
                    # Format: "YYYY-MM-DD"
                    dt = datetime.strptime(dt_str, "%Y-%m-%d")
            except ValueError:
                # Try parsing from timestamp
                timestamp = hour_data.get("timestamp", 0)
                if timestamp:
                    dt = datetime.fromtimestamp(timestamp)
                else:
                    continue
            
            # Make timezone aware
            if dt.tzinfo is None:
                dt = self.timezone.localize(dt)
            
            date_str = dt.strftime("%Y-%m-%d")
            
            hourly = HourlyWeather(
                datetime=dt,
                date_str=date_str,
                hour=dt.hour,
                temperature=hour_data.get("temperature", 0),
                feels_like=hour_data.get("feels_like", 0),
                humidity=hour_data.get("humidity", 0),
                dew_point=hour_data.get("dew_point", 0),
                wind_speed=hour_data.get("wind_speed", 0),
                wind_gust=hour_data.get("wind_gust", 0),
                wind_direction=int(hour_data.get("wind_direction", 0)),
                cloud_base_m=float(hour_data.get("cloud_base_m", 0)),
                fog_probability=float(hour_data.get("fog_probability", 0)),
                precipitation_probability=hour_data.get("precipitation_probability", 0),
                visibility=hour_data.get("visibility", 10),
                precipitation_mm=hour_data.get("precipitation_mm", hour_data.get("rain_mm", 0)),
                snow_mm=hour_data.get("snow_mm", 0),
                pressure=hour_data.get("pressure", 1013),
                uv_index=hour_data.get("uv_index", 0),
                weather_condition=hour_data.get("weather_condition", ""),
                weather_description=hour_data.get("weather_description", ""),
                source=source
            )
            
            hourly_list.append(hourly)
        
        return hourly_list
    
    def _group_by_date(self, hourly_list: List[HourlyWeather]) -> Dict[str, List[HourlyWeather]]:
        """Group hourly data by date."""
        by_date = defaultdict(list)
        for h in hourly_list:
            by_date[h.date_str].append(h)
        return dict(by_date)
    
    def _find_flyable_hours_for_day(
        self,
        location: Location,
        ow_day_data: List[HourlyWeather],
        vc_day_data: List[HourlyWeather]
    ) -> Tuple[List[int], List[int], List[int]]:
        """
        Find flyable hours for a single day per source.
        Returns (hours_both, hours_ow_only, hours_vc_only).
        """
        ow_by_hour = {h.hour: h for h in ow_day_data}
        vc_by_hour = {h.hour: h for h in vc_day_data}
        start_hour = location.time_window_start
        end_hour = location.time_window_end
        
        hours_both = []
        hours_ow_only = []
        hours_vc_only = []
        
        for hour in range(start_hour, end_hour + 1):
            ow_hour = ow_by_hour.get(hour)
            vc_hour = vc_by_hour.get(hour)
            
            ow_flyable = self._check_hour_flyable(ow_hour, location) if ow_hour else False
            vc_flyable = self._check_hour_flyable(vc_hour, location) if vc_hour else False
            
            if ow_flyable and vc_flyable:
                hours_both.append(hour)
            elif ow_flyable:
                hours_ow_only.append(hour)
            elif vc_flyable:
                hours_vc_only.append(hour)
        
        return (hours_both, hours_ow_only, hours_vc_only)
    
    def _check_hour_flyable(self, weather: HourlyWeather, location: Location) -> bool:
        """Check if all conditions are met for a single hour."""
        # Temperature (minimum only; no upper limit)
        if weather.temperature < location.temp_min:
            return False
        
        # Humidity
        if weather.humidity > location.humidity_max:
            return False
        
        # Wind speed
        if weather.wind_speed > location.wind_speed_max:
            return False
        
        # Wind gust
        gust_limit = location.wind_speed_max * 1.5
        if weather.wind_gust > gust_limit:
            return False
        
        # Wind direction
        allowed_directions = location.get_wind_directions_list()
        if allowed_directions:
            if not self._check_wind_direction(
                weather.wind_direction,
                allowed_directions,
                location.wind_direction_tolerance
            ):
                return False
        
        # Dew point spread
        dew_spread = weather.temperature - weather.dew_point
        if dew_spread < location.dew_point_spread_min:
            return False
        
        # Precipitation probability
        if weather.precipitation_probability > location.precipitation_probability_max:
            return False
        
        return True
    
    def _check_wind_direction(
        self, 
        actual: int, 
        allowed: List[int], 
        tolerance: int
    ) -> bool:
        """Check if wind direction is within allowed directions."""
        for allowed_dir in allowed:
            diff = abs(actual - allowed_dir)
            if diff > 180:
                diff = 360 - diff
            if diff <= tolerance:
                return True
        return False
    
    def _window_source(
        self,
        window_hours: set,
        hours_both: set,
        hours_ow_only: set,
        hours_vc_only: set
    ) -> str:
        """Determine source label for a window: both, openweather, visualcrossing, or mixed."""
        if not window_hours:
            return "both"
        in_both = bool(window_hours & hours_both)
        in_ow = bool(window_hours & hours_ow_only) or in_both
        in_vc = bool(window_hours & hours_vc_only) or in_both
        if window_hours <= hours_both:
            return "both"
        if window_hours <= (hours_both | hours_ow_only) and not (window_hours & hours_vc_only):
            return "openweather"
        if window_hours <= (hours_both | hours_vc_only) and not (window_hours & hours_ow_only):
            return "visualcrossing"
        return "mixed"

    def _find_continuous_windows(
        self,
        date_str: str,
        flyable_hours: List[int],
        required_hours: int,
        all_hourly_data: List[HourlyWeather],
        source: str = "both"
    ) -> List[FlyableWindowInfo]:
        """Find continuous windows of flyable hours and calculate statistics."""
        if len(flyable_hours) < required_hours:
            return []
        
        sorted_hours = sorted(flyable_hours)
        windows = []
        
        current_sequence = [sorted_hours[0]]
        for i in range(1, len(sorted_hours)):
            if sorted_hours[i] == sorted_hours[i-1] + 1:
                current_sequence.append(sorted_hours[i])
            else:
                if len(current_sequence) >= required_hours:
                    window = self._create_window_info(
                        date_str, current_sequence, all_hourly_data, source=source
                    )
                    windows.append(window)
                current_sequence = [sorted_hours[i]]
        
        if len(current_sequence) >= required_hours:
            window = self._create_window_info(
                date_str, current_sequence, all_hourly_data, source=source
            )
            windows.append(window)
        
        return windows
    
    def _create_window_info(
        self,
        date_str: str,
        hours: List[int],
        all_hourly_data: List[HourlyWeather],
        source: str = "both"
    ) -> FlyableWindowInfo:
        """Create a FlyableWindowInfo with statistics from the window hours."""
        window_data = [
            h for h in all_hourly_data
            if h.date_str == date_str and h.hour in hours
        ]
        
        window = FlyableWindowInfo(
            date=date_str,
            start_hour=hours[0],
            end_hour=hours[-1],
            duration_hours=len(hours),
            source=source
        )
        
        if window_data:
            temps = [h.temperature for h in window_data]
            winds = [h.wind_speed for h in window_data]
            humidities = [h.humidity for h in window_data]
            precip_probs = [h.precipitation_probability for h in window_data]
            cloud_bases = [h.cloud_base_m for h in window_data]
            fog_probs = [h.fog_probability for h in window_data]
            window.avg_temp = sum(temps) / len(temps)
            window.min_temp = min(temps)
            window.max_temp = max(temps)
            window.avg_wind_speed = sum(winds) / len(winds)
            window.max_wind_speed = max(winds)
            window.avg_humidity = sum(humidities) / len(humidities)
            window.max_precipitation_prob = max(precip_probs)
            window.avg_cloud_base_m = sum(cloud_bases) / len(cloud_bases)
            window.max_fog_probability = max(fog_probs) if fog_probs else 0
        
        return window
    
    def _set_current_conditions(
        self, 
        result: FullForecastAnalysis,
        ow_hourly: List[HourlyWeather],
        vc_hourly: List[HourlyWeather]
    ) -> None:
        """Set current weather conditions from nearest hour."""
        now = datetime.now(self.timezone)
        current_hour = now.hour
        today_str = now.strftime("%Y-%m-%d")
        
        # Find nearest hour in OpenWeather data
        for hourly in ow_hourly:
            if hourly.date_str == today_str and hourly.hour == current_hour:
                result.current_temp = hourly.temperature
                result.current_wind_speed = hourly.wind_speed
                result.current_wind_direction = hourly.wind_direction
                result.current_humidity = hourly.humidity
                result.current_cloud_base_m = hourly.cloud_base_m
                result.current_fog_probability = hourly.fog_probability
                break
        
        # Fallback to VisualCrossing
        if result.current_temp is None:
            for hourly in vc_hourly:
                if hourly.date_str == today_str and hourly.hour == current_hour:
                    result.current_temp = hourly.temperature
                    result.current_wind_speed = hourly.wind_speed
                    result.current_wind_direction = hourly.wind_direction
                    result.current_humidity = hourly.humidity
                    result.current_cloud_base_m = hourly.cloud_base_m
                    result.current_fog_probability = hourly.fog_probability
                    break
    
    def get_wind_direction_name(self, degrees: int) -> str:
        """Convert wind direction in degrees to compass name."""
        directions = [
            "С", "ССВ", "СВ", "ВСВ",
            "В", "ВЮВ", "ЮВ", "ЮЮВ",
            "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ",
            "З", "ЗСЗ", "СЗ", "ССЗ"
        ]
        idx = round(degrees / 22.5) % 16
        return directions[idx]
    
    # Legacy method for backward compatibility
    def analyze(
        self,
        location: Location,
        openweather_data: Optional[Dict[str, Any]],
        visualcrossing_data: Optional[Dict[str, Any]],
        check_date: Optional[datetime] = None
    ):
        """Legacy method - redirects to analyze_full_forecast."""
        return self.analyze_full_forecast(location, openweather_data, visualcrossing_data)
