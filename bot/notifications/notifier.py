"""
Notification manager for sending weather alerts.
Handles state tracking and notification logic.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
import pytz

from .templates import MessageTemplates
from ..database import Database, Location, WeatherStatus, WeatherCheck
from ..weather import WeatherAnalyzer, AnalysisResult
from ..weather import OpenWeatherClient, VisualCrossingClient
from ..config import Config

logger = logging.getLogger(__name__)


class Notifier:
    """
    Manages weather notifications and state tracking.
    
    Notification logic:
    - Send "flyable" notification when conditions become flyable
    - Send "not flyable" notification only if:
      1. Previous notification was "flyable"
      2. Last TWO checks confirmed not flyable
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
    
    async def check_all_locations(self) -> None:
        """
        Check weather for all active locations and send notifications if needed.
        """
        logger.info("Starting weather check for all locations")
        
        locations = await self.db.get_all_active_locations()
        logger.info(f"Found {len(locations)} active locations")
        
        for location in locations:
            try:
                await self.check_location(location)
                # Delay between API calls to avoid rate limiting
                await asyncio.sleep(self.api_delay)
            except Exception as e:
                logger.error(f"Error checking location {location.name}: {e}")
    
    async def check_location(self, location: Location) -> AnalysisResult:
        """
        Check weather for a single location and handle notifications.
        
        Args:
            location: Location to check
        
        Returns:
            Analysis result
        """
        logger.info(f"Checking weather for location: {location.name}")
        
        # Fetch weather data from both sources
        ow_data = await self.openweather.get_hourly_forecast(
            location.latitude, location.longitude
        )
        
        await asyncio.sleep(self.api_delay)
        
        vc_data = await self.visualcrossing.get_hourly_forecast(
            location.latitude, location.longitude
        )
        
        # Analyze the data
        result = self.analyzer.analyze(location, ow_data, vc_data)
        
        # Record the check in database
        check = WeatherCheck(
            location_id=location.id,
            check_time=datetime.now(self.timezone),
            openweather_data=json.dumps(ow_data) if ow_data else "{}",
            visualcrossing_data=json.dumps(vc_data) if vc_data else "{}",
            is_flyable=result.is_flyable,
            flyable_hours=json.dumps(result.flyable_hours)
        )
        check.set_rejection_reasons_list(result.rejection_reasons)
        await self.db.create_weather_check(check)
        
        # Handle state change and notifications
        await self._handle_state_change(location, result)
        
        return result
    
    async def _handle_state_change(
        self, 
        location: Location, 
        result: AnalysisResult
    ) -> None:
        """
        Handle weather state changes and send notifications.
        
        Logic:
        - If flyable now and wasn't before → send "flyable" notification
        - If not flyable now and was flyable before:
          - Increment consecutive_not_flyable counter
          - If counter >= 2 → send "not flyable" notification
        
        Args:
            location: Location being checked
            result: Analysis result
        """
        today = result.date
        
        # Get current status from database
        status = await self.db.get_weather_status(location.id, today)
        
        if status is None:
            # First check of the day
            status = WeatherStatus(
                location_id=location.id,
                date=today,
                is_flyable=result.is_flyable,
                flyable_window_start=result.flyable_window_start,
                flyable_window_end=result.flyable_window_end,
                consecutive_not_flyable_checks=0 if result.is_flyable else 1
            )
            
            # Send notification for new flyable status
            if result.is_flyable:
                status.last_notification_type = "flyable"
                status.last_notification_at = datetime.now(self.timezone)
                await self._send_flyable_notification(location, result)
            
            await self.db.upsert_weather_status(status)
            return
        
        # Check for state changes
        was_flyable = status.is_flyable
        is_flyable_now = result.is_flyable
        
        if is_flyable_now:
            # Conditions are flyable
            if not was_flyable or status.last_notification_type != "flyable":
                # Became flyable - send notification
                status.is_flyable = True
                status.flyable_window_start = result.flyable_window_start
                status.flyable_window_end = result.flyable_window_end
                status.consecutive_not_flyable_checks = 0
                status.last_notification_type = "flyable"
                status.last_notification_at = datetime.now(self.timezone)
                
                await self._send_flyable_notification(location, result)
                logger.info(f"Sent flyable notification for {location.name}")
            else:
                # Still flyable - just update window if changed
                status.flyable_window_start = result.flyable_window_start
                status.flyable_window_end = result.flyable_window_end
                status.consecutive_not_flyable_checks = 0
        else:
            # Conditions are not flyable
            status.consecutive_not_flyable_checks += 1
            status.is_flyable = False
            status.flyable_window_start = None
            status.flyable_window_end = None
            
            # Only send "not flyable" if:
            # 1. Previous notification was "flyable"
            # 2. We've had 2 consecutive not flyable checks
            if (status.last_notification_type == "flyable" and 
                status.consecutive_not_flyable_checks >= 2):
                
                status.last_notification_type = "not_flyable"
                status.last_notification_at = datetime.now(self.timezone)
                
                await self._send_not_flyable_notification(location, result)
                logger.info(f"Sent not-flyable notification for {location.name}")
        
        # Save updated status
        await self.db.upsert_weather_status(status)
    
    async def _send_flyable_notification(
        self, 
        location: Location, 
        result: AnalysisResult
    ) -> None:
        """
        Send a "flyable weather" notification to the chat.
        
        Args:
            location: Location data
            result: Analysis result
        """
        try:
            # Get chat settings for custom template
            settings = await self.db.get_chat_settings(location.chat_id)
            template = settings.flyable_template if settings else None
            
            message = MessageTemplates.format_flyable_message(
                result, location, template, self.timezone
            )
            
            await self.bot.send_message(
                chat_id=location.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            logger.info(f"Sent flyable notification to chat {location.chat_id}")
        
        except TelegramError as e:
            logger.error(f"Failed to send flyable notification: {e}")
    
    async def _send_not_flyable_notification(
        self, 
        location: Location, 
        result: AnalysisResult
    ) -> None:
        """
        Send a "not flyable weather" notification to the chat.
        
        Args:
            location: Location data
            result: Analysis result
        """
        try:
            # Get chat settings for custom template
            settings = await self.db.get_chat_settings(location.chat_id)
            template = settings.not_flyable_template if settings else None
            
            message = MessageTemplates.format_not_flyable_message(
                result, location, template, self.timezone
            )
            
            await self.bot.send_message(
                chat_id=location.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            logger.info(f"Sent not-flyable notification to chat {location.chat_id}")
        
        except TelegramError as e:
            logger.error(f"Failed to send not-flyable notification: {e}")
    
    async def send_status_message(
        self, 
        chat_id: int, 
        location: Location, 
        result: AnalysisResult
    ) -> None:
        """
        Send a status check message (manual check, not notification).
        
        Args:
            chat_id: Chat to send to
            location: Location data
            result: Analysis result
        """
        try:
            message = MessageTemplates.format_status_message(
                result, location, self.timezone
            )
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
        except TelegramError as e:
            logger.error(f"Failed to send status message: {e}")
            raise
    
    async def get_location_status(self, location: Location) -> Optional[AnalysisResult]:
        """
        Get current weather status for a location without sending notifications.
        
        Args:
            location: Location to check
        
        Returns:
            Analysis result or None on error
        """
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
            return self.analyzer.analyze(location, ow_data, vc_data)
        
        except Exception as e:
            logger.error(f"Error getting location status: {e}")
            return None
