"""
OpenWeatherMap API client.
Fetches hourly weather forecasts for specified locations.
"""

import aiohttp
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class OpenWeatherClient:
    """Client for OpenWeatherMap API."""
    
    BASE_URL = "https://api.openweathermap.org/data/2.5"
    
    def __init__(self, api_key: str):
        """
        Initialize OpenWeather client.
        
        Args:
            api_key: OpenWeatherMap API key
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
        hours: int = 48
    ) -> Optional[Dict[str, Any]]:
        """
        Get hourly weather forecast for a location.
        
        Args:
            latitude: Location latitude
            longitude: Location longitude
            hours: Number of hours to forecast (max 48 for free tier)
        
        Returns:
            Dictionary with hourly forecast data or None on error
        """
        session = await self._get_session()
        
        # Use One Call API 3.0 for hourly data
        # Fall back to forecast API if One Call is not available
        try:
            # First try the forecast endpoint (free tier)
            url = f"{self.BASE_URL}/forecast"
            params = {
                "lat": latitude,
                "lon": longitude,
                "appid": self.api_key,
                "units": "metric",  # Celsius
                "cnt": min(hours // 3, 40)  # 3-hour intervals, max 40
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_forecast_response(data)
                else:
                    error_text = await response.text()
                    logger.error(
                        f"OpenWeather API error: {response.status} - {error_text}"
                    )
                    return None
        
        except aiohttp.ClientError as e:
            logger.error(f"OpenWeather request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"OpenWeather unexpected error: {e}")
            return None
    
    def _parse_forecast_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse OpenWeather forecast response into standardized format.
        
        Args:
            data: Raw API response
        
        Returns:
            Standardized forecast data
        """
        hourly_data = []
        
        for item in data.get("list", []):
            # Extract main weather data
            main = item.get("main", {})
            wind = item.get("wind", {})
            clouds = item.get("clouds", {})
            rain = item.get("rain", {})
            snow = item.get("snow", {})
            
            # Calculate precipitation probability
            pop = item.get("pop", 0) * 100  # Convert to percentage
            
            # Calculate dew point if not provided
            temp = main.get("temp", 0)
            humidity = main.get("humidity", 0)
            
            # Approximate dew point calculation using Magnus formula
            if humidity > 0:
                import math
                a = 17.27
                b = 237.7
                alpha = ((a * temp) / (b + temp)) + math.log(humidity / 100.0)
                dew_point = (b * alpha) / (a - alpha)
            else:
                dew_point = temp
            
            hourly_item = {
                "datetime": item.get("dt_txt", ""),
                "timestamp": item.get("dt", 0),
                "temperature": temp,
                "feels_like": main.get("feels_like", temp),
                "humidity": humidity,
                "dew_point": round(dew_point, 1),
                "wind_speed": wind.get("speed", 0),
                "wind_gust": wind.get("gust", wind.get("speed", 0)),
                "wind_direction": wind.get("deg", 0),
                "cloud_cover": clouds.get("all", 0),
                "precipitation_probability": pop,
                "rain_mm": rain.get("3h", 0),
                "snow_mm": snow.get("3h", 0),
                "visibility": item.get("visibility", 10000) / 1000,  # Convert to km
                "weather_condition": item.get("weather", [{}])[0].get("main", ""),
                "weather_description": item.get("weather", [{}])[0].get("description", ""),
            }
            
            hourly_data.append(hourly_item)
        
        return {
            "source": "openweather",
            "city": data.get("city", {}).get("name", ""),
            "country": data.get("city", {}).get("country", ""),
            "timezone_offset": data.get("city", {}).get("timezone", 0),
            "hourly": hourly_data,
            "fetched_at": datetime.utcnow().isoformat()
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
        session = await self._get_session()
        
        try:
            url = f"{self.BASE_URL}/weather"
            params = {
                "lat": latitude,
                "lon": longitude,
                "appid": self.api_key,
                "units": "metric"
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_current_response(data)
                else:
                    error_text = await response.text()
                    logger.error(
                        f"OpenWeather current API error: {response.status} - {error_text}"
                    )
                    return None
        
        except Exception as e:
            logger.error(f"OpenWeather current weather error: {e}")
            return None
    
    def _parse_current_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse current weather response."""
        main = data.get("main", {})
        wind = data.get("wind", {})
        clouds = data.get("clouds", {})
        
        temp = main.get("temp", 0)
        humidity = main.get("humidity", 0)
        
        # Calculate dew point
        if humidity > 0:
            import math
            a = 17.27
            b = 237.7
            alpha = ((a * temp) / (b + temp)) + math.log(humidity / 100.0)
            dew_point = (b * alpha) / (a - alpha)
        else:
            dew_point = temp
        
        return {
            "source": "openweather",
            "temperature": temp,
            "feels_like": main.get("feels_like", temp),
            "humidity": humidity,
            "dew_point": round(dew_point, 1),
            "wind_speed": wind.get("speed", 0),
            "wind_gust": wind.get("gust", wind.get("speed", 0)),
            "wind_direction": wind.get("deg", 0),
            "cloud_cover": clouds.get("all", 0),
            "visibility": data.get("visibility", 10000) / 1000,
            "weather_condition": data.get("weather", [{}])[0].get("main", ""),
            "weather_description": data.get("weather", [{}])[0].get("description", ""),
            "fetched_at": datetime.utcnow().isoformat()
        }
