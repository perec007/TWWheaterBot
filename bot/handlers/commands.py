"""
Telegram bot command handlers.
Handles all bot commands from users.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Update, Chat
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatType

from ..config import Config
from ..database import Database, Location
from ..notifications import Notifier, MessageTemplates

logger = logging.getLogger(__name__)


class CommandHandlers:
    """
    Handles all Telegram bot commands.
    
    Commands are only accepted from:
    - Channel/group admins
    - Private messages
    - Global admins (from .env)
    """
    
    def __init__(self, db: Database, notifier: Notifier):
        """
        Initialize command handlers.
        
        Args:
            db: Database instance
            notifier: Notifier instance
        """
        self.db = db
        self.notifier = notifier
    
    async def _is_authorized(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """
        Check if user is authorized to use commands.
        
        Authorized users:
        - Global admins (from ADMIN_USER_IDS env)
        - Channel/group admins
        - Private chat users
        
        Args:
            update: Telegram update
            context: Bot context
        
        Returns:
            True if authorized
        """
        user_id = update.effective_user.id
        chat = update.effective_chat
        
        # Global admins always authorized
        if user_id in Config.ADMIN_USER_IDS:
            return True
        
        # Private chats - authorized
        if chat.type == ChatType.PRIVATE:
            return True
        
        # For groups/channels - check if user is admin
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
            try:
                member = await context.bot.get_chat_member(chat.id, user_id)
                return member.status in ("administrator", "creator")
            except Exception as e:
                logger.warning(f"Could not check admin status: {e}")
                return False
        
        return False
    
    async def _send_unauthorized(self, update: Update) -> None:
        """Send unauthorized message."""
        await update.message.reply_text(
            "⛔ Эта команда доступна только администраторам.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    async def start_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Handle /start command.
        Sends welcome message and registers chat.
        """
        user = update.effective_user
        chat = update.effective_chat
        
        # Register or update chat settings
        await self.db.get_or_create_chat_settings(
            chat_id=chat.id,
            chat_type=chat.type,
            chat_title=chat.title or user.first_name
        )
        
        # Send welcome message
        message = MessageTemplates.format_welcome_message(
            user.first_name or "пилот"
        )
        
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        logger.info(f"User {user.id} started bot in chat {chat.id}")
    
    async def help_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Handle /help command.
        Sends wind directions image, then help message with command list.
        """
        # Path to wind rose image (bot/static/wind_directions.png)
        static_dir = Path(__file__).resolve().parent.parent / "static"
        wind_image_path = static_dir / "wind_directions.png"
        if wind_image_path.is_file():
            try:
                with open(wind_image_path, "rb") as photo_file:
                    await update.message.reply_photo(
                        photo=photo_file,
                        caption="🧭 *Направления ветра*\nС \\= Север, В \\= Восток, Ю \\= Юг, З \\= Запад\nСВ, ЮВ, ЮЗ, СЗ \\= промежуточные",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
            except Exception as e:
                logger.warning(f"Could not send wind directions image: {e}")

        message = MessageTemplates.format_help_message()
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    
    async def list_locations_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Handle /list_locations command.
        Shows all configured locations for this chat.
        """
        if not await self._is_authorized(update, context):
            await self._send_unauthorized(update)
            return
        
        chat = update.effective_chat
        locations = await self.db.get_locations_by_chat(chat.id, active_only=False)
        
        settings = await self.db.get_chat_settings(chat.id)
        chat_title = settings.chat_title if settings else None
        
        message = MessageTemplates.format_location_list(locations, chat_title)
        
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    async def status_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Handle /status command.
        Shows current weather status for all locations.
        """
        if not await self._is_authorized(update, context):
            await self._send_unauthorized(update)
            return
        
        chat = update.effective_chat
        locations = await self.db.get_locations_by_chat(chat.id)
        
        if not locations:
            await update.message.reply_text(
                "📍 Нет настроенных локаций\\.\n\n"
                "Используйте /set\\_config для добавления локаций\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        await update.message.reply_text(
            "🔄 Проверяю погоду\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        for location in locations:
            try:
                result = await self.notifier.get_location_status(location)
                if result:
                    await self.notifier.send_status_message(
                        chat.id, location, result
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Не удалось получить данные для *{MessageTemplates.escape_markdown(location.name)}*",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
            except Exception as e:
                logger.error(f"Error getting status for {location.name}: {e}")
                await update.message.reply_text(
                    f"❌ Ошибка при проверке *{MessageTemplates.escape_markdown(location.name)}*: {MessageTemplates.escape_markdown(str(e))}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
    
    async def check_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Handle /check command.
        Triggers weather check for all locations (may send notifications).
        """
        if not await self._is_authorized(update, context):
            await self._send_unauthorized(update)
            return
        
        chat = update.effective_chat
        locations = await self.db.get_locations_by_chat(chat.id)
        
        if not locations:
            await update.message.reply_text(
                "📍 Нет настроенных локаций\\.\n\n"
                "Используйте /set\\_config для добавления локаций\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        await update.message.reply_text(
            f"🔄 Запускаю проверку погоды для {len(locations)} локаций\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        results = []
        for location in locations:
            try:
                result = await self.notifier.check_location(location)
                status = "✅" if result.has_flyable_conditions else "❌"
                windows_info = ""
                if result.flyable_windows:
                    windows_info = f" ({len(result.flyable_windows)} окон)"
                results.append(f"{status} {location.name}{windows_info}")
            except Exception as e:
                logger.error(f"Error checking {location.name}: {e}")
                results.append(f"⚠️ {location.name}: ошибка")
        
        # Send summary
        summary = "📊 *Результаты проверки:*\n\n" + "\n".join(
            [MessageTemplates.escape_markdown(r) for r in results]
        )
        
        await update.message.reply_text(
            summary,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    async def flywindow_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Handle /flywindow command.
        Shows all current flyable windows with full weather details.
        """
        if not await self._is_authorized(update, context):
            await self._send_unauthorized(update)
            return

        chat = update.effective_chat
        locations = await self.db.get_locations_by_chat(chat.id)

        if not locations:
            await update.message.reply_text(
                "📍 Нет настроенных локаций\\.\n\n"
                "Используйте /set\\_config для добавления локаций\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await update.message.reply_text(
            "🔄 Проверяю погоду\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        locations_with_windows = []
        for location in locations:
            try:
                result = await self.notifier.get_location_status(location)
                if result and result.flyable_windows:
                    locations_with_windows.append((location, result))
            except Exception as e:
                logger.error(f"Error getting flywindow for {location.name}: {e}")

        if not locations_with_windows:
            await update.message.reply_text(
                "🪂 *Лётные окна*\n\nНет подходящих лётных окон в прогнозе\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        timezone = Config.get_timezone()
        message = MessageTemplates.format_flywindow_message(
            locations_with_windows, timezone
        )
        max_len = 4096
        if len(message) <= max_len:
            await update.message.reply_text(
                message,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            parts = []
            current = []
            current_len = 0
            for line in message.split("\n"):
                line_len = len(line) + 1
                if current_len + line_len > max_len and current:
                    parts.append("\n".join(current))
                    current = []
                    current_len = 0
                current.append(line)
                current_len += line_len
            if current:
                parts.append("\n".join(current))
            for part in parts:
                await update.message.reply_text(
                    part,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

    async def get_config_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Handle /get_config command.
        Shows current configuration for all locations.
        """
        if not await self._is_authorized(update, context):
            await self._send_unauthorized(update)
            return
        
        chat = update.effective_chat
        locations = await self.db.get_locations_by_chat(chat.id, active_only=False)
        
        if not locations:
            await update.message.reply_text(
                "📍 Нет настроенных локаций\\.\n\n"
                "Используйте /set\\_config для добавления локаций\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        for location in locations:
            message = MessageTemplates.format_config_message(location)
            await update.message.reply_text(
                message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
    
    async def weather_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Handle /weather command.
        Shows current weather for a location.
        Usage: /weather [location_name] or /weather (shows all)
        """
        if not await self._is_authorized(update, context):
            await self._send_unauthorized(update)
            return
        
        chat = update.effective_chat
        locations = await self.db.get_locations_by_chat(chat.id)
        
        if not locations:
            await update.message.reply_text(
                "📍 Нет настроенных локаций\\.\n\n"
                "Используйте /set\\_config для добавления локаций\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        # Check if specific location requested
        location_name = " ".join(context.args) if context.args else None
        
        if location_name:
            # Find specific location
            location = next(
                (loc for loc in locations if loc.name.lower() == location_name.lower()),
                None
            )
            if not location:
                # Try partial match
                location = next(
                    (loc for loc in locations if location_name.lower() in loc.name.lower()),
                    None
                )
            
            if not location:
                available = ", ".join([loc.name for loc in locations])
                await update.message.reply_text(
                    f"❌ Локация *{MessageTemplates.escape_markdown(location_name)}* не найдена\\.\n\n"
                    f"Доступные локации: {MessageTemplates.escape_markdown(available)}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return
            
            locations = [location]
        
        await update.message.reply_text(
            "🌤 Получаю данные о погоде\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        for location in locations:
            try:
                weather_data = await self._get_current_weather(location)
                if weather_data:
                    message = MessageTemplates.format_current_weather(
                        location, weather_data, self.notifier.timezone
                    )
                    await update.message.reply_text(
                        message,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Не удалось получить погоду для *{MessageTemplates.escape_markdown(location.name)}*",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
            except Exception as e:
                logger.error(f"Error getting weather for {location.name}: {e}")
                await update.message.reply_text(
                    f"❌ Ошибка: {MessageTemplates.escape_markdown(str(e))}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
    
    async def _get_current_weather(self, location: Location) -> dict:
        """Get current weather from both APIs and combine."""
        import asyncio
        
        ow_data = await self.notifier.openweather.get_current_weather(
            location.latitude, location.longitude
        )
        
        await asyncio.sleep(self.notifier.api_delay)
        
        vc_data = await self.notifier.visualcrossing.get_current_weather(
            location.latitude, location.longitude
        )
        
        if not ow_data and not vc_data:
            return None
        
        # Combine data, prefer OpenWeather as primary
        result = {
            "temperature": None,
            "feels_like": None,
            "humidity": None,
            "wind_speed": None,
            "wind_gust": None,
            "wind_direction": None,
            "cloud_base_m": None,
            "fog_probability": None,
            "pressure": None,
            "visibility": None,
            "dew_point": None,
            "weather_condition": "",
            "weather_description": "",
            "sources": []
        }
        
        if ow_data:
            result["temperature"] = ow_data.get("temperature")
            result["feels_like"] = ow_data.get("feels_like")
            result["humidity"] = ow_data.get("humidity")
            result["wind_speed"] = ow_data.get("wind_speed")
            result["wind_gust"] = ow_data.get("wind_gust")
            result["wind_direction"] = ow_data.get("wind_direction")
            result["cloud_base_m"] = ow_data.get("cloud_base_m")
            result["fog_probability"] = ow_data.get("fog_probability")
            result["dew_point"] = ow_data.get("dew_point")
            result["visibility"] = ow_data.get("visibility")
            result["weather_condition"] = ow_data.get("weather_condition", "")
            result["weather_description"] = ow_data.get("weather_description", "")
            result["sources"].append("OpenWeather")
        
        if vc_data:
            # Fill in missing data from VisualCrossing
            if result["temperature"] is None:
                result["temperature"] = vc_data.get("temperature")
            if result["feels_like"] is None:
                result["feels_like"] = vc_data.get("feels_like")
            if result["humidity"] is None:
                result["humidity"] = vc_data.get("humidity")
            if result["wind_speed"] is None:
                result["wind_speed"] = vc_data.get("wind_speed")
            if result["wind_direction"] is None:
                result["wind_direction"] = vc_data.get("wind_direction")
            if result["cloud_base_m"] is None:
                result["cloud_base_m"] = vc_data.get("cloud_base_m")
            if result["fog_probability"] is None:
                result["fog_probability"] = vc_data.get("fog_probability")
            if result["pressure"] is None:
                result["pressure"] = vc_data.get("pressure")
            if result["dew_point"] is None:
                result["dew_point"] = vc_data.get("dew_point")
            result["sources"].append("VisualCrossing")
        
        return result
    
    async def unknown_command(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle unknown commands."""
        await update.message.reply_text(
            "❓ Неизвестная команда\\. Используйте /help для списка команд\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
