"""
Visual Crossing Weather API client.
Fetches hourly weather forecasts for specified locations.
"""

import aiohttp
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class VisualCrossingClient:
    """Client for Visual Crossing Weather API."""
    
    BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
    
    def __init__(self, api_key: str):
        """
        Initialize Visual Crossing client.
        
        Args:
            api_key: Visual Crossing API key
        """
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_hourly_forecast(
        self, 
        latitude: float, 
        longitude: float,
        days: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Get hourly weather forecast for a location.
        
        Args:
            latitude: Location latitude
            longitude: Location longitude
            days: Number of days to forecast (1-15)
        
        Returns:
            Dictionary with hourly forecast data or None on error
        """
        session = await self._get_session()
        
        try:
            # Build location string
            location = f"{latitude},{longitude}"
            
            # Date range: today to today+days
            today = datetime.utcnow().strftime("%Y-%m-%d")
            end_date = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
            
            url = f"{self.BASE_URL}/{location}/{today}/{end_date}"
            
            params = {
                "key": self.api_key,
                "unitGroup": "metric",
                "include": "hours,current",
                "contentType": "json"
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_forecast_response(data)
                else:
                    error_text = await response.text()
                    logger.error(
                        f"VisualCrossing API error: {response.status} - {error_text}"
                    )
                    return None
        
        except aiohttp.ClientError as e:
            logger.error(f"VisualCrossing request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"VisualCrossing unexpected error: {e}")
            return None
    
    def _parse_forecast_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Visual Crossing forecast response into standardized format.
        
        Args:
            data: Raw API response
        
        Returns:
            Standardized forecast data
        """
        hourly_data = []
        
        # Process each day
        for day in data.get("days", []):
            day_date = day.get("datetime", "")
            
            # Process hourly data for this day
            for hour in day.get("hours", []):
                hour_time = hour.get("datetime", "00:00:00")
                
                # Combine date and time
                datetime_str = f"{day_date} {hour_time}"
                
                # Convert epoch if available
                timestamp = hour.get("datetimeEpoch", 0)
                
                hourly_item = {
                    "datetime": datetime_str,
                    "timestamp": timestamp,
                    "temperature": hour.get("temp", 0),
                    "feels_like": hour.get("feelslike", hour.get("temp", 0)),
                    "humidity": hour.get("humidity", 0),
                    "dew_point": hour.get("dew", 0),
                    "wind_speed": hour.get("windspeed", 0) / 3.6,  # Convert km/h to m/s
                    "wind_gust": hour.get("windgust", 0) / 3.6 if hour.get("windgust") else 0,
                    "wind_direction": hour.get("winddir", 0),
                    "cloud_cover": hour.get("cloudcover", 0),
                    "precipitation_probability": hour.get("precipprob", 0),
                    "precipitation_mm": hour.get("precip", 0) or 0,
                    "snow_mm": hour.get("snow", 0) or 0,
                    "visibility": hour.get("visibility", 10),
                    "uv_index": hour.get("uvindex", 0),
                    "pressure": hour.get("pressure", 1013),
                    "weather_condition": hour.get("conditions", ""),
                    "weather_icon": hour.get("icon", ""),
                }
                
                hourly_data.append(hourly_item)
        
        return {
            "source": "visualcrossing",
            "address": data.get("resolvedAddress", ""),
            "timezone": data.get("timezone", "UTC"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "hourly": hourly_data,
            "current": self._parse_current_conditions(data.get("currentConditions", {})),
            "fetched_at": datetime.utcnow().isoformat()
        }
    
    def _parse_current_conditions(self, current: Dict[str, Any]) -> Dict[str, Any]:
        """Parse current conditions from response."""
        if not current:
            return {}
        
        return {
            "temperature": current.get("temp", 0),
            "feels_like": current.get("feelslike", current.get("temp", 0)),
            "humidity": current.get("humidity", 0),
            "dew_point": current.get("dew", 0),
            "wind_speed": current.get("windspeed", 0) / 3.6,  # Convert km/h to m/s
            "wind_gust": current.get("windgust", 0) / 3.6 if current.get("windgust") else 0,
            "wind_direction": current.get("winddir", 0),
            "cloud_cover": current.get("cloudcover", 0),
            "visibility": current.get("visibility", 10),
            "uv_index": current.get("uvindex", 0),
            "pressure": current.get("pressure", 1013),
            "weather_condition": current.get("conditions", ""),
        }
    
    async def get_current_weather(
        self, 
        latitude: float, 
        longitude: float
    ) -> Optional[Dict[str, Any]]:
        """
        Get current weather for a location.
        
        Args:
            latitude: Location latitude
            longitude: Location longitude
        
        Returns:
            Dictionary with current weather data or None on error
        """
        # Use the hourly forecast endpoint but only get current day
        forecast = await self.get_hourly_forecast(latitude, longitude, days=1)
        
        if forecast and forecast.get("current"):
            return {
                "source": "visualcrossing",
                **forecast["current"],
                "fetched_at": datetime.utcnow().isoformat()
            }
        
        return None
