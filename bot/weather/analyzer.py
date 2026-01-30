"""
Weather analyzer for determining flyable conditions.
Combines data from multiple sources and evaluates against location-specific rules.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import pytz

from ..database.models import Location

logger = logging.getLogger(__name__)


@dataclass
class HourlyWeather:
    """Standardized hourly weather data from any source."""
    datetime: datetime
    hour: int  # 0-23
    temperature: float
    feels_like: float
    humidity: float
    dew_point: float
    wind_speed: float  # m/s
    wind_gust: float  # m/s
    wind_direction: int  # degrees
    cloud_cover: float  # percentage
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
class HourlyAnalysis:
    """Analysis result for a single hour."""
    hour: int
    datetime: datetime
    is_flyable: bool
    checks: List[ConditionCheck] = field(default_factory=list)
    failed_conditions: List[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Complete analysis result for a location."""
    location_id: int
    location_name: str
    date: str
    is_flyable: bool
    flyable_window_start: Optional[str] = None  # HH:MM
    flyable_window_end: Optional[str] = None  # HH:MM
    flyable_hours: List[int] = field(default_factory=list)
    continuous_hours: int = 0
    rejection_reasons: List[str] = field(default_factory=list)
    hourly_analysis: List[HourlyAnalysis] = field(default_factory=list)
    
    # Current conditions summary
    current_temp: Optional[float] = None
    current_wind_speed: Optional[float] = None
    current_wind_direction: Optional[int] = None
    current_humidity: Optional[float] = None
    current_cloud_cover: Optional[float] = None
    
    # Data sources used
    openweather_available: bool = False
    visualcrossing_available: bool = False


class WeatherAnalyzer:
    """
    Analyzes weather data to determine flyable conditions.
    
    The analyzer combines data from multiple sources and checks against
    location-specific rules to determine if conditions are suitable for flying.
    
    Key features:
    - Requires both data sources to agree on flyable conditions
    - Finds continuous time windows where all conditions are met
    - Tracks rejection reasons for user feedback
    """
    
    def __init__(self, timezone: pytz.timezone = pytz.UTC):
        """
        Initialize the weather analyzer.
        
        Args:
            timezone: Timezone for interpreting time windows
        """
        self.timezone = timezone
    
    def analyze(
        self,
        location: Location,
        openweather_data: Optional[Dict[str, Any]],
        visualcrossing_data: Optional[Dict[str, Any]],
        check_date: Optional[datetime] = None
    ) -> AnalysisResult:
        """
        Analyze weather data for a location.
        
        Args:
            location: Location with weather rules
            openweather_data: Parsed OpenWeather API response
            visualcrossing_data: Parsed VisualCrossing API response
            check_date: Date to analyze (defaults to today)
        
        Returns:
            AnalysisResult with flyable determination and details
        """
        if check_date is None:
            check_date = datetime.now(self.timezone)
        
        date_str = check_date.strftime("%Y-%m-%d")
        
        result = AnalysisResult(
            location_id=location.id,
            location_name=location.name,
            date=date_str,
            is_flyable=False,
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
        
        # Parse hourly data from both sources
        ow_hourly = self._parse_hourly_data(openweather_data, "openweather", check_date)
        vc_hourly = self._parse_hourly_data(visualcrossing_data, "visualcrossing", check_date)
        
        if not ow_hourly or not vc_hourly:
            result.rejection_reasons.append("❌ Нет почасовых данных в прогнозах")
            return result
        
        # Get time window hours
        start_hour = location.time_window_start
        end_hour = location.time_window_end
        
        # Analyze each hour in the time window
        flyable_hours = []
        
        for hour in range(start_hour, end_hour + 1):
            ow_hour_data = self._get_hour_data(ow_hourly, hour)
            vc_hour_data = self._get_hour_data(vc_hourly, hour)
            
            if not ow_hour_data or not vc_hour_data:
                continue
            
            # Check conditions against both sources
            ow_analysis = self._check_hour_conditions(ow_hour_data, location)
            vc_analysis = self._check_hour_conditions(vc_hour_data, location)
            
            # Hour is flyable only if BOTH sources say it's flyable
            hour_flyable = ow_analysis.is_flyable and vc_analysis.is_flyable
            
            # Combine failed conditions from both sources
            combined_failed = list(set(ow_analysis.failed_conditions + vc_analysis.failed_conditions))
            
            hourly_result = HourlyAnalysis(
                hour=hour,
                datetime=ow_hour_data.datetime,
                is_flyable=hour_flyable,
                checks=ow_analysis.checks + vc_analysis.checks,
                failed_conditions=combined_failed
            )
            
            result.hourly_analysis.append(hourly_result)
            
            if hour_flyable:
                flyable_hours.append(hour)
        
        result.flyable_hours = flyable_hours
        
        # Find continuous window
        continuous_window = self._find_continuous_window(
            flyable_hours, 
            location.required_conditions_duration_hours
        )
        
        if continuous_window:
            result.is_flyable = True
            result.flyable_window_start = f"{continuous_window[0]:02d}:00"
            result.flyable_window_end = f"{continuous_window[-1]:02d}:00"
            result.continuous_hours = len(continuous_window)
        else:
            # Build rejection reasons
            if not flyable_hours:
                result.rejection_reasons.append(
                    f"❌ Нет часов, удовлетворяющих всем критериям в окне {start_hour:02d}:00-{end_hour:02d}:00"
                )
            else:
                result.rejection_reasons.append(
                    f"❌ Требуется {location.required_conditions_duration_hours} непрерывных часов, "
                    f"доступно только {len(flyable_hours)} час(ов) в разное время"
                )
            
            # Add specific failed conditions
            self._add_detailed_rejection_reasons(result, location)
        
        # Add current conditions from the nearest hour
        self._set_current_conditions(result, ow_hourly, vc_hourly)
        
        return result
    
    def _parse_hourly_data(
        self, 
        data: Dict[str, Any], 
        source: str,
        target_date: datetime
    ) -> List[HourlyWeather]:
        """Parse raw API data into standardized hourly format."""
        hourly_list = []
        target_date_str = target_date.strftime("%Y-%m-%d")
        
        for hour_data in data.get("hourly", []):
            # Parse datetime
            dt_str = hour_data.get("datetime", "")
            
            # Handle different datetime formats
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
            
            # Filter to target date
            if dt.strftime("%Y-%m-%d") != target_date_str:
                continue
            
            hourly = HourlyWeather(
                datetime=dt,
                hour=dt.hour,
                temperature=hour_data.get("temperature", 0),
                feels_like=hour_data.get("feels_like", 0),
                humidity=hour_data.get("humidity", 0),
                dew_point=hour_data.get("dew_point", 0),
                wind_speed=hour_data.get("wind_speed", 0),
                wind_gust=hour_data.get("wind_gust", 0),
                wind_direction=int(hour_data.get("wind_direction", 0)),
                cloud_cover=hour_data.get("cloud_cover", 0),
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
    
    def _get_hour_data(
        self, 
        hourly_list: List[HourlyWeather], 
        hour: int
    ) -> Optional[HourlyWeather]:
        """Get data for a specific hour."""
        for h in hourly_list:
            if h.hour == hour:
                return h
        return None
    
    def _check_hour_conditions(
        self, 
        weather: HourlyWeather, 
        location: Location
    ) -> HourlyAnalysis:
        """Check all conditions for a single hour."""
        checks = []
        failed_conditions = []
        
        # Temperature check
        temp_check = ConditionCheck(
            name="temperature",
            passed=location.temp_min <= weather.temperature <= location.temp_max,
            actual_value=weather.temperature,
            limit_value=f"{location.temp_min}-{location.temp_max}°C",
            message=f"Температура: {weather.temperature}°C"
        )
        checks.append(temp_check)
        if not temp_check.passed:
            if weather.temperature < location.temp_min:
                failed_conditions.append(f"🌡 Слишком холодно: {weather.temperature}°C (мин. {location.temp_min}°C)")
            else:
                failed_conditions.append(f"🌡 Слишком жарко: {weather.temperature}°C (макс. {location.temp_max}°C)")
        
        # Humidity check
        humidity_check = ConditionCheck(
            name="humidity",
            passed=weather.humidity <= location.humidity_max,
            actual_value=weather.humidity,
            limit_value=f"≤{location.humidity_max}%",
            message=f"Влажность: {weather.humidity}%"
        )
        checks.append(humidity_check)
        if not humidity_check.passed:
            failed_conditions.append(f"💧 Высокая влажность: {weather.humidity}% (макс. {location.humidity_max}%)")
        
        # Wind speed check
        wind_check = ConditionCheck(
            name="wind_speed",
            passed=weather.wind_speed <= location.wind_speed_max,
            actual_value=weather.wind_speed,
            limit_value=f"≤{location.wind_speed_max} м/с",
            message=f"Ветер: {weather.wind_speed:.1f} м/с"
        )
        checks.append(wind_check)
        if not wind_check.passed:
            failed_conditions.append(f"💨 Сильный ветер: {weather.wind_speed:.1f} м/с (макс. {location.wind_speed_max} м/с)")
        
        # Wind gust check (using same limit as wind speed * 1.5)
        gust_limit = location.wind_speed_max * 1.5
        gust_check = ConditionCheck(
            name="wind_gust",
            passed=weather.wind_gust <= gust_limit,
            actual_value=weather.wind_gust,
            limit_value=f"≤{gust_limit:.1f} м/с",
            message=f"Порывы: {weather.wind_gust:.1f} м/с"
        )
        checks.append(gust_check)
        if not gust_check.passed:
            failed_conditions.append(f"💨 Сильные порывы: {weather.wind_gust:.1f} м/с (макс. {gust_limit:.1f} м/с)")
        
        # Wind direction check
        allowed_directions = location.get_wind_directions_list()
        if allowed_directions:
            direction_ok = self._check_wind_direction(
                weather.wind_direction,
                allowed_directions,
                location.wind_direction_tolerance
            )
            direction_check = ConditionCheck(
                name="wind_direction",
                passed=direction_ok,
                actual_value=weather.wind_direction,
                limit_value=f"{allowed_directions}° ±{location.wind_direction_tolerance}°",
                message=f"Направление: {weather.wind_direction}°"
            )
            checks.append(direction_check)
            if not direction_check.passed:
                failed_conditions.append(
                    f"🧭 Неподходящее направление ветра: {weather.wind_direction}° "
                    f"(допустимо: {allowed_directions}°)"
                )
        
        # Dew point spread check
        dew_spread = weather.temperature - weather.dew_point
        dew_check = ConditionCheck(
            name="dew_point_spread",
            passed=dew_spread >= location.dew_point_spread_min,
            actual_value=dew_spread,
            limit_value=f"≥{location.dew_point_spread_min}°C",
            message=f"Разница с точкой росы: {dew_spread:.1f}°C"
        )
        checks.append(dew_check)
        if not dew_check.passed:
            failed_conditions.append(
                f"🌫 Близко к точке росы: разница {dew_spread:.1f}°C "
                f"(мин. {location.dew_point_spread_min}°C)"
            )
        
        # Precipitation probability check
        precip_check = ConditionCheck(
            name="precipitation_probability",
            passed=weather.precipitation_probability <= location.precipitation_probability_max,
            actual_value=weather.precipitation_probability,
            limit_value=f"≤{location.precipitation_probability_max}%",
            message=f"Вероятность осадков: {weather.precipitation_probability}%"
        )
        checks.append(precip_check)
        if not precip_check.passed:
            failed_conditions.append(
                f"🌧 Высокая вероятность осадков: {weather.precipitation_probability}% "
                f"(макс. {location.precipitation_probability_max}%)"
            )
        
        # Cloud cover check
        cloud_check = ConditionCheck(
            name="cloud_cover",
            passed=weather.cloud_cover <= location.cloud_cover_max,
            actual_value=weather.cloud_cover,
            limit_value=f"≤{location.cloud_cover_max}%",
            message=f"Облачность: {weather.cloud_cover}%"
        )
        checks.append(cloud_check)
        if not cloud_check.passed:
            failed_conditions.append(
                f"☁️ Высокая облачность: {weather.cloud_cover}% "
                f"(макс. {location.cloud_cover_max}%)"
            )
        
        # Determine if all conditions pass
        all_passed = all(check.passed for check in checks)
        
        return HourlyAnalysis(
            hour=weather.hour,
            datetime=weather.datetime,
            is_flyable=all_passed,
            checks=checks,
            failed_conditions=failed_conditions
        )
    
    def _check_wind_direction(
        self, 
        actual: int, 
        allowed: List[int], 
        tolerance: int
    ) -> bool:
        """
        Check if wind direction is within allowed directions.
        Handles wraparound at 360 degrees.
        """
        for allowed_dir in allowed:
            diff = abs(actual - allowed_dir)
            # Handle wraparound
            if diff > 180:
                diff = 360 - diff
            if diff <= tolerance:
                return True
        return False
    
    def _find_continuous_window(
        self, 
        flyable_hours: List[int], 
        required_hours: int
    ) -> Optional[List[int]]:
        """
        Find a continuous window of flyable hours.
        
        Returns the first continuous window of at least required_hours length,
        or None if no such window exists.
        """
        if len(flyable_hours) < required_hours:
            return None
        
        # Sort hours
        sorted_hours = sorted(flyable_hours)
        
        # Find continuous sequences
        current_sequence = [sorted_hours[0]]
        best_sequence = []
        
        for i in range(1, len(sorted_hours)):
            if sorted_hours[i] == sorted_hours[i-1] + 1:
                # Continuous
                current_sequence.append(sorted_hours[i])
            else:
                # Gap found
                if len(current_sequence) >= required_hours:
                    if not best_sequence or len(current_sequence) > len(best_sequence):
                        best_sequence = current_sequence.copy()
                current_sequence = [sorted_hours[i]]
        
        # Check last sequence
        if len(current_sequence) >= required_hours:
            if not best_sequence or len(current_sequence) > len(best_sequence):
                best_sequence = current_sequence.copy()
        
        return best_sequence if best_sequence else None
    
    def _add_detailed_rejection_reasons(
        self, 
        result: AnalysisResult, 
        location: Location
    ) -> None:
        """Add detailed rejection reasons based on hourly analysis."""
        # Collect all unique failed conditions
        condition_counts = {}
        
        for hour_analysis in result.hourly_analysis:
            for condition in hour_analysis.failed_conditions:
                # Extract condition type (first word/emoji)
                condition_key = condition.split(":")[0].strip()
                if condition_key not in condition_counts:
                    condition_counts[condition_key] = []
                condition_counts[condition_key].append(hour_analysis.hour)
        
        # Add most common problems
        for condition_key, hours in sorted(
            condition_counts.items(), 
            key=lambda x: -len(x[1])
        ):
            if len(hours) > len(result.hourly_analysis) // 2:
                hours_str = ", ".join(f"{h:02d}:00" for h in sorted(hours)[:5])
                if len(hours) > 5:
                    hours_str += f" и ещё {len(hours) - 5} час(ов)"
                result.rejection_reasons.append(
                    f"{condition_key} в часы: {hours_str}"
                )
    
    def _set_current_conditions(
        self, 
        result: AnalysisResult,
        ow_hourly: List[HourlyWeather],
        vc_hourly: List[HourlyWeather]
    ) -> None:
        """Set current weather conditions in result from nearest hour."""
        now = datetime.now(self.timezone)
        current_hour = now.hour
        
        # Find nearest hour in data
        for hourly in ow_hourly:
            if hourly.hour == current_hour:
                result.current_temp = hourly.temperature
                result.current_wind_speed = hourly.wind_speed
                result.current_wind_direction = hourly.wind_direction
                result.current_humidity = hourly.humidity
                result.current_cloud_cover = hourly.cloud_cover
                break
        
        # If not found in OpenWeather, try VisualCrossing
        if result.current_temp is None:
            for hourly in vc_hourly:
                if hourly.hour == current_hour:
                    result.current_temp = hourly.temperature
                    result.current_wind_speed = hourly.wind_speed
                    result.current_wind_direction = hourly.wind_direction
                    result.current_humidity = hourly.humidity
                    result.current_cloud_cover = hourly.cloud_cover
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
