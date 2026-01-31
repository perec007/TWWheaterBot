"""
Notification manager for sending weather alerts.
Handles state tracking and notification logic.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
import pytz

from .templates import MessageTemplates
from ..database import Database, Location, WeatherStatus, WeatherCheck, WeatherForecast, FlyableWindow
from ..weather import WeatherAnalyzer
from ..weather.analyzer import FullForecastAnalysis, FlyableWindowInfo
from ..weather import OpenWeatherClient, VisualCrossingClient
from ..config import Config

logger = logging.getLogger(__name__)


class DiagnosticMessage:
    """Helper class to format diagnostic messages."""
    
    @staticmethod
    def escape_md(text: str) -> str:
        """Escape MarkdownV2 special characters."""
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = str(text).replace(char, f'\\{char}')
        return text
    
    @classmethod
    def format_api_response(
        cls,
        source: str,
        location_name: str,
        lat: float,
        lon: float,
        data: Optional[Dict[str, Any]],
        error: Optional[str] = None
    ) -> str:
        """Format API response for diagnostic message."""
        esc = cls.escape_md
        
        if error:
            return f"""🔴 *API ERROR: {esc(source)}*

📍 *Локация:* {esc(location_name)}
🌐 *Координаты:* {esc(f'{lat}, {lon}')}
❌ *Ошибка:* {esc(error)}
⏰ *Время:* {esc(datetime.now().strftime('%H:%M:%S'))}"""
        
        if not data:
            return f"""🟡 *API NO DATA: {esc(source)}*

📍 *Локация:* {esc(location_name)}
🌐 *Координаты:* {esc(f'{lat}, {lon}')}
⚠️ *Статус:* Нет данных
⏰ *Время:* {esc(datetime.now().strftime('%H:%M:%S'))}"""
        
        # Extract key info from response
        hourly = data.get("hourly", [])
        first_hour = hourly[0] if hourly else {}
        
        lines = [
            f"🟢 *API OK: {esc(source)}*",
            "",
            f"📍 *Локация:* {esc(location_name)}",
            f"🌐 *Координаты:* {esc(f'{lat}, {lon}')}",
            f"📊 *Часов в прогнозе:* {len(hourly)}",
        ]
        
        if first_hour:
            lines.extend([
                "",
                "*Ближайший час:*",
                f"🌡 Темп: {esc(str(first_hour.get('temperature', 'N/A')))}°C",
                f"💧 Влажность: {esc(str(first_hour.get('humidity', 'N/A')))}%",
                f"🌧 Осадки: {esc(str(first_hour.get('precipitation_probability', 'N/A')))}%",
                f"☁️ Высота облаков: {esc(str(first_hour.get('cloud_base_m', 'N/A')))} м",
                f"🌫 Туман: {esc(str(first_hour.get('fog_probability', 'N/A')))}%",
                f"💨 Ветер: {esc(str(first_hour.get('wind_speed', 'N/A')))} м/с, {esc(MessageTemplates._get_wind_direction_name(int(first_hour.get('wind_direction', 0))))}",
            ])
        
        lines.extend([
            "",
            f"⏰ *Время:* {esc(datetime.now().strftime('%H:%M:%S'))}"
        ])
        
        return "\n".join(lines)
    
    @classmethod
    def format_analysis_result(
        cls,
        location_name: str,
        result: FullForecastAnalysis
    ) -> str:
        """Format analysis result for diagnostic message."""
        esc = cls.escape_md
        
        status_emoji = "✅" if result.has_flyable_conditions else "❌"
        status_text = "ЕСТЬ ЛЁТНЫЕ ОКНА" if result.has_flyable_conditions else "НЕТ ЛЁТНЫХ ОКОН"
        
        lines = [
            f"📊 *АНАЛИЗ: {status_emoji} {esc(status_text)}*",
            "",
            f"📍 *Локация:* {esc(location_name)}",
            f"📅 *Прогноз:* {esc(result.forecast_start.strftime('%Y-%m-%d'))} — {esc(result.forecast_end.strftime('%Y-%m-%d'))}",
            f"📊 *Проанализировано часов:* {result.total_hours_analyzed}",
            f"✈️ *Лётных часов:* {result.total_flyable_hours}",
            f"🪟 *Лётных окон:* {len(result.flyable_windows)}",
        ]
        
        if result.flyable_windows:
            lines.append("")
            lines.append("*Окна:*")
            for i, window in enumerate(result.flyable_windows[:5]):  # Limit to 5
                source_label = MessageTemplates._source_label(getattr(window, "source", "both"))
                lines.append(f"• {esc(window.to_display_string())} \\({esc(source_label)}\\)")
            if len(result.flyable_windows) > 5:
                lines.append(f"\\.\\.\\. и ещё {len(result.flyable_windows) - 5}")
        else:
            lines.append("")
            lines.append("*Причины:*")
            for reason in result.rejection_reasons[:3]:
                lines.append(f"• {esc(reason)}")
        
        lines.extend([
            "",
            f"⏰ *Время:* {esc(datetime.now().strftime('%H:%M:%S'))}"
        ])
        
        return "\n".join(lines)


class Notifier:
    """
    Manages weather notifications and state tracking.
    
    Notification logic:
    - Send "flyable" notification when new flyable windows appear in forecast
    - Send "not flyable" notification when previously announced windows disappear
    - Track all windows across the entire forecast period
    """
    
    def __init__(
        self,
        bot: Bot,
        db: Database,
        openweather: OpenWeatherClient,
        visualcrossing: VisualCrossingClient,
        timezone: pytz.timezone = pytz.UTC
    ):
        """
        Initialize the notifier.
        
        Args:
            bot: Telegram bot instance
            db: Database instance
            openweather: OpenWeather API client
            visualcrossing: VisualCrossing API client
            timezone: Timezone for notifications
        """
        self.bot = bot
        self.db = db
        self.openweather = openweather
        self.visualcrossing = visualcrossing
        self.timezone = timezone
        self.analyzer = WeatherAnalyzer(timezone)
        self.api_delay = Config.API_REQUEST_DELAY_SECONDS
        self.debug_mode = Config.DEBUG_MODE
        self.admin_ids = Config.ADMIN_USER_IDS
        
        logger.debug(f"Notifier initialized: debug_mode={self.debug_mode}, admin_ids={self.admin_ids}")
    
    async def _send_diagnostic(self, message: str) -> None:
        """Send diagnostic message to all admin users."""
        if not self.debug_mode:
            logger.debug("Debug mode disabled, skipping diagnostic")
            return
        
        if not self.admin_ids:
            logger.debug("No admin IDs configured, skipping diagnostic")
            return
        
        logger.debug(f"Sending diagnostic to {len(self.admin_ids)} admins")
        
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.debug(f"Diagnostic sent to admin {admin_id}")
            except TelegramError as e:
                logger.warning(f"Failed to send diagnostic to admin {admin_id}: {e}")
    
    async def check_all_locations(self) -> None:
        """Check weather for all active locations and send notifications if needed."""
        logger.debug("Starting weather check for all locations")
        
        locations = await self.db.get_all_active_locations()
        logger.debug(f"Found {len(locations)} active locations")
        
        for location in locations:
            try:
                await self.check_location(location)
                # Delay between API calls to avoid rate limiting
                await asyncio.sleep(self.api_delay)
            except Exception as e:
                logger.error(f"Error checking location {location.name}: {e}")
    
    async def check_location(self, location: Location) -> FullForecastAnalysis:
        """
        Check weather for a single location and handle notifications.
        
        Args:
            location: Location to check
        
        Returns:
            Analysis result
        """
        logger.debug(f"Checking weather for location: {location.name}")
        
        # Fetch weather data from OpenWeather
        ow_data = None
        ow_error = None
        try:
            ow_data = await self.openweather.get_hourly_forecast(
                location.latitude, location.longitude
            )
        except Exception as e:
            ow_error = str(e)
            logger.error(f"OpenWeather API error for {location.name}: {e}")
        
        # Send diagnostic for OpenWeather
        if self.debug_mode:
            diag_msg = DiagnosticMessage.format_api_response(
                source="OpenWeather",
                location_name=location.name,
                lat=location.latitude,
                lon=location.longitude,
                data=ow_data,
                error=ow_error
            )
            await self._send_diagnostic(diag_msg)
        
        await asyncio.sleep(self.api_delay)
        
        # Fetch weather data from VisualCrossing
        vc_data = None
        vc_error = None
        try:
            vc_data = await self.visualcrossing.get_hourly_forecast(
                location.latitude, location.longitude
            )
        except Exception as e:
            vc_error = str(e)
            logger.error(f"VisualCrossing API error for {location.name}: {e}")
        
        # Send diagnostic for VisualCrossing
        if self.debug_mode:
            diag_msg = DiagnosticMessage.format_api_response(
                source="VisualCrossing",
                location_name=location.name,
                lat=location.latitude,
                lon=location.longitude,
                data=vc_data,
                error=vc_error
            )
            await self._send_diagnostic(diag_msg)
        
        # Analyze the full forecast
        result = self.analyzer.analyze_full_forecast(location, ow_data, vc_data)
        
        # Send diagnostic for analysis result
        if self.debug_mode:
            diag_msg = DiagnosticMessage.format_analysis_result(
                location_name=location.name,
                result=result
            )
            await self._send_diagnostic(diag_msg)
        
        # Store forecast in database
        forecast = await self._store_forecast(location, result, ow_data, vc_data)
        
        # Handle notifications
        await self._handle_forecast_changes(location, result, forecast)
        
        return result
    
    async def _store_forecast(
        self,
        location: Location,
        result: FullForecastAnalysis,
        ow_data: Optional[Dict],
        vc_data: Optional[Dict]
    ) -> WeatherForecast:
        """Store forecast and flyable windows in database."""
        # Create forecast record
        windows_json = [w.to_dict() for w in result.flyable_windows]
        
        forecast = WeatherForecast(
            location_id=location.id,
            check_time=result.analysis_time,
            forecast_start=result.forecast_start,
            forecast_end=result.forecast_end,
            openweather_data=json.dumps(ow_data) if ow_data else "{}",
            visualcrossing_data=json.dumps(vc_data) if vc_data else "{}",
            total_flyable_windows=len(result.flyable_windows),
            flyable_windows_json=json.dumps(windows_json, default=str)
        )
        
        forecast = await self.db.create_weather_forecast(forecast)
        
        # Create flyable window records
        for window_info in result.flyable_windows:
            window = FlyableWindow(
                location_id=location.id,
                forecast_id=forecast.id,
                date=window_info.date,
                start_hour=window_info.start_hour,
                end_hour=window_info.end_hour,
                duration_hours=window_info.duration_hours,
                source=getattr(window_info, "source", "both"),
                avg_temp=window_info.avg_temp,
                avg_wind_speed=window_info.avg_wind_speed,
                max_wind_speed=window_info.max_wind_speed,
                avg_humidity=window_info.avg_humidity,
                max_precipitation_prob=window_info.max_precipitation_prob
            )
            await self.db.create_flyable_window(window)
        
        return forecast
    
    async def _handle_forecast_changes(
        self,
        location: Location,
        result: FullForecastAnalysis,
        forecast: WeatherForecast
    ) -> None:
        """
        Compare current forecast with previous and send appropriate notifications.
        
        Logic:
        - New windows that weren't in previous forecast → send "flyable" notification
        - Windows that were notified but are gone → send "not flyable" notification
        """
        now = datetime.now(self.timezone)
        today_str = now.strftime("%Y-%m-%d")
        
        # Get current windows as dicts for comparison
        current_windows = [w.to_dict() for w in result.flyable_windows]
        
        # Get previously notified windows that haven't been cancelled
        notified_windows = await self.db.get_notified_windows(location.id)
        
        # Find windows that are no longer in forecast (need cancellation notification)
        cancelled_windows = await self.db.cancel_windows_not_in_forecast(
            location.id,
            current_windows,
            now
        )
        
        # Find new windows (not yet notified)
        notified_keys = set()
        for w in notified_windows:
            notified_keys.add((w.date, w.start_hour, w.end_hour, getattr(w, "source", "both")))
        
        new_windows = []
        for window_info in result.flyable_windows:
            key = (window_info.date, window_info.start_hour, window_info.end_hour, getattr(window_info, "source", "both"))
            if key not in notified_keys:
                new_windows.append(window_info)
        
        # Send one combined message for new and/or cancelled windows
        if new_windows or cancelled_windows:
            await self._send_windows_update_notification(
                location, new_windows, cancelled_windows, result
            )
            if new_windows:
                active_windows = await self.db.get_active_flyable_windows(location.id)
                for db_window in active_windows:
                    for new_w in new_windows:
                        if (db_window.date == new_w.date and
                            db_window.start_hour == new_w.start_hour and
                            db_window.end_hour == new_w.end_hour and
                            getattr(db_window, "source", "both") == getattr(new_w, "source", "both") and
                            not db_window.notified):
                            await self.db.mark_window_notified(db_window.id, now)
            logger.info(
                f"🪂 {location.name}: {len(new_windows)} new, {len(cancelled_windows)} cancelled"
            )
        
        # Update weather status
        await self._update_weather_status(location, result, forecast, current_windows)
    
    async def _send_windows_update_notification(
        self,
        location: Location,
        new_windows: List[FlyableWindowInfo],
        cancelled_windows: List[FlyableWindow],
        result: FullForecastAnalysis
    ) -> None:
        """Send one combined notification for new and/or cancelled flyable windows."""
        try:
            message = MessageTemplates.format_windows_update_message(
                location=location,
                new_windows=new_windows,
                cancelled_windows=cancelled_windows,
                total_windows=len(result.flyable_windows),
                timezone=self.timezone
            )
            if not message:
                return
            await self.bot.send_message(
                chat_id=location.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(
                f"Sent windows update to chat {location.chat_id}: "
                f"{len(new_windows)} new, {len(cancelled_windows)} cancelled"
            )
        except TelegramError as e:
            logger.error(f"Failed to send windows update notification: {e}")
    
    async def _send_new_windows_notification(
        self,
        location: Location,
        new_windows: List[FlyableWindowInfo],
        result: FullForecastAnalysis
    ) -> None:
        """Send notification about new flyable windows (legacy, use _send_windows_update_notification)."""
        try:
            settings = await self.db.get_chat_settings(location.chat_id)
            
            message = MessageTemplates.format_new_windows_message(
                location=location,
                new_windows=new_windows,
                total_windows=len(result.flyable_windows),
                timezone=self.timezone
            )
            
            await self.bot.send_message(
                chat_id=location.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            logger.debug(f"Sent new windows notification to chat {location.chat_id}")
        
        except TelegramError as e:
            logger.error(f"Failed to send new windows notification: {e}")
    
    async def _send_window_cancelled_notification(
        self,
        location: Location,
        window: FlyableWindow
    ) -> None:
        """Send notification that a previously announced window is cancelled."""
        try:
            message = MessageTemplates.format_window_cancelled_message(
                location=location,
                window=window,
                timezone=self.timezone
            )
            
            await self.bot.send_message(
                chat_id=location.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            logger.info(f"❌ {location.chat_id}: window cancelled")
        
        except TelegramError as e:
            logger.error(f"Failed to send cancellation notification: {e}")
    
    async def _update_weather_status(
        self,
        location: Location,
        result: FullForecastAnalysis,
        forecast: WeatherForecast,
        current_windows: List[dict]
    ) -> None:
        """Update weather status in database."""
        now = datetime.now(self.timezone)
        today_str = now.strftime("%Y-%m-%d")
        
        # Get or create status
        status = await self.db.get_weather_status(location.id, today_str)
        
        if status is None:
            status = WeatherStatus(
                location_id=location.id,
                date=today_str,
                is_flyable=result.has_flyable_conditions,
                consecutive_not_flyable_checks=0 if result.has_flyable_conditions else 1
            )
        else:
            status.is_flyable = result.has_flyable_conditions
            if result.has_flyable_conditions:
                status.consecutive_not_flyable_checks = 0
            else:
                status.consecutive_not_flyable_checks += 1
        
        # Update with current windows
        status.set_active_windows(current_windows)
        status.last_forecast_id = forecast.id
        
        # Set first window as the "main" window for backward compatibility
        if result.flyable_windows:
            first = result.flyable_windows[0]
            status.flyable_window_start = f"{first.start_hour:02d}:00"
            status.flyable_window_end = f"{first.end_hour:02d}:00"
        else:
            status.flyable_window_start = None
            status.flyable_window_end = None
        
        await self.db.upsert_weather_status(status)
    
    async def send_status_message(
        self, 
        chat_id: int, 
        location: Location, 
        result: FullForecastAnalysis
    ) -> None:
        """Send a status check message (manual check, not notification)."""
        try:
            message = MessageTemplates.format_forecast_status_message(
                result=result,
                location=location,
                timezone=self.timezone
            )
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
        except TelegramError as e:
            logger.error(f"Failed to send status message: {e}")
            raise
    
    async def get_location_status(self, location: Location) -> Optional[FullForecastAnalysis]:
        """Get current weather status for a location without sending notifications."""
        try:
            # Fetch weather data
            ow_data = await self.openweather.get_hourly_forecast(
                location.latitude, location.longitude
            )
            
            await asyncio.sleep(self.api_delay)
            
            vc_data = await self.visualcrossing.get_hourly_forecast(
                location.latitude, location.longitude
            )
            
            # Analyze without state tracking
            return self.analyzer.analyze_full_forecast(location, ow_data, vc_data)
        
        except Exception as e:
            logger.error(f"Error getting location status: {e}")
            return None
