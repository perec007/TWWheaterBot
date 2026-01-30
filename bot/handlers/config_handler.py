"""
Configuration handler for setting up locations via Telegram.
Handles /set_config command and TOML configuration parsing.
"""

import json
import logging
from typing import Dict, Any, List, Optional

import toml
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, 
    ConversationHandler, 
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from telegram.constants import ParseMode, ChatType

from ..config import Config
from ..database import Database, Location, ChatSettings
from ..notifications import MessageTemplates

logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_CONFIG = 1
WAITING_FOR_DELETE_CONFIRM = 2

# Callback data prefixes
CONFIRM_DELETE_PREFIX = "cfg_del_yes_"
CANCEL_DELETE_PREFIX = "cfg_del_no_"


class ConfigHandler:
    """
    Handles location configuration via Telegram messages.
    
    Users can send a TOML configuration to set up locations.
    """
    
    def __init__(self, db: Database):
        """
        Initialize config handler.
        
        Args:
            db: Database instance
        """
        self.db = db
    
    async def _is_authorized(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Check if user is authorized to configure."""
        user_id = update.effective_user.id
        chat = update.effective_chat
        
        if user_id in Config.ADMIN_USER_IDS:
            return True
        
        if chat.type == ChatType.PRIVATE:
            return True
        
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
            try:
                member = await context.bot.get_chat_member(chat.id, user_id)
                return member.status in ("administrator", "creator")
            except Exception:
                return False
        
        return False
    
    def get_conversation_handler(self) -> ConversationHandler:
        """
        Create conversation handler for /set_config command.
        
        Returns:
            ConversationHandler instance
        """
        return ConversationHandler(
            entry_points=[
                CommandHandler("set_config", self.start_config)
            ],
            states={
                WAITING_FOR_CONFIG: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.receive_config
                    ),
                    MessageHandler(
                        filters.Document.ALL,
                        self.receive_config_file
                    ),
                ],
                WAITING_FOR_DELETE_CONFIRM: [
                    CallbackQueryHandler(
                        self.handle_delete_confirmation,
                        pattern=f"^({CONFIRM_DELETE_PREFIX}|{CANCEL_DELETE_PREFIX})"
                    ),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", self.cancel_config)
            ],
            per_chat=True,
            per_user=True
        )
    
    def _generate_toml_config(self, locations: list, settings: Optional[ChatSettings] = None) -> str:
        """
        Generate TOML configuration from locations list.
        """
        lines = ["# Конфигурация погодного бота"]
        lines.append(f"notifications_enabled = {str(settings.notifications_enabled if settings else True).lower()}")
        lines.append("")
        
        for loc in locations:
            lines.append("[[locations]]")
            lines.append(f'name = "{loc.name}"')
            lines.append(f"latitude = {loc.latitude}")
            lines.append(f"longitude = {loc.longitude}")
            lines.append(f"time_window_start = {loc.time_window_start}")
            lines.append(f"time_window_end = {loc.time_window_end}")
            lines.append(f"temp_min = {loc.temp_min}")
            lines.append(f"temp_max = {loc.temp_max}")
            lines.append(f"humidity_max = {loc.humidity_max}")
            lines.append(f"wind_speed_max = {loc.wind_speed_max}")
            
            wind_dirs = loc.get_wind_directions_list()
            lines.append(f"wind_directions = {wind_dirs}")
            
            lines.append(f"wind_direction_tolerance = {loc.wind_direction_tolerance}")
            lines.append(f"dew_point_spread_min = {loc.dew_point_spread_min}")
            lines.append(f"required_conditions_duration_hours = {loc.required_conditions_duration_hours}")
            lines.append(f"precipitation_probability_max = {loc.precipitation_probability_max}")
            lines.append(f"cloud_cover_max = {loc.cloud_cover_max}")
            lines.append(f"is_active = {str(loc.is_active).lower()}")
            lines.append("")
        
        return "\n".join(lines)
    
    async def start_config(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Handle /set_config command - show current config and wait for new one.
        """
        if not await self._is_authorized(update, context):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администраторам\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return ConversationHandler.END
        
        chat = update.effective_chat
        
        # Get current locations and settings
        locations = await self.db.get_locations_by_chat(chat.id, active_only=False)
        settings = await self.db.get_chat_settings(chat.id)
        
        if locations:
            # Show current config
            current_config = self._generate_toml_config(locations, settings)
            
            message = f"""⚙️ *Текущая конфигурация:*

```toml
{MessageTemplates.escape_markdown(current_config)}
```

Отправьте новую TOML конфигурацию для обновления\\.
Можно отправить как текст или файл \\.toml\\.

_Используйте /help для справки по параметрам_
_Используйте /cancel для отмены_"""
        else:
            # No config yet
            message = """⚙️ *Настройка конфигурации*

📍 Локации ещё не настроены\\.

Отправьте TOML конфигурацию для настройки локаций\\.
Можно отправить как текст или файл \\.toml\\.

_Используйте /help для справки и примера конфигурации_
_Используйте /cancel для отмены_"""
        
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        return WAITING_FOR_CONFIG
    
    def _normalize_text(self, text: str) -> str:
        """
        Normalize text by replacing typographic quotes and other common issues.
        """
        # Replace various quote styles with standard double quotes
        replacements = {
            '«': '"',  # Russian opening guillemet
            '»': '"',  # Russian closing guillemet
            '"': '"',  # Left double quotation mark
            '"': '"',  # Right double quotation mark
            '„': '"',  # Double low-9 quotation mark
            ''': "'",  # Left single quotation mark
            ''': "'",  # Right single quotation mark
            '‚': "'",  # Single low-9 quotation mark
            '‹': "'",  # Single left-pointing angle quotation mark
            '›': "'",  # Single right-pointing angle quotation mark
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        return text
    
    async def receive_config(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Receive and process TOML configuration from message text.
        """
        text = update.message.text
        
        # Normalize quotes and other special characters
        text = self._normalize_text(text)
        
        try:
            config = toml.loads(text)
            return await self._process_config(update, context, config)
        except toml.TomlDecodeError as e:
            await update.message.reply_text(
                f"❌ Ошибка парсинга TOML: {MessageTemplates.escape_markdown(str(e))}\n\n"
                "Убедитесь, что используете прямые кавычки \" а не «» или ""\n\n"
                "Отправьте корректный TOML или /cancel для отмены\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return WAITING_FOR_CONFIG
    
    async def receive_config_file(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Receive and process TOML configuration from file.
        """
        try:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            text = content.decode('utf-8')
            
            # Normalize quotes and other special characters
            text = self._normalize_text(text)
            
            # Try TOML first, then JSON for backward compatibility
            try:
                config = toml.loads(text)
            except toml.TomlDecodeError:
                # Try JSON as fallback
                try:
                    config = json.loads(text)
                except json.JSONDecodeError:
                    raise ValueError("Файл не является корректным TOML или JSON")
            
            return await self._process_config(update, context, config)
        except ValueError as e:
            await update.message.reply_text(
                f"❌ {MessageTemplates.escape_markdown(str(e))}\n\n"
                "Отправьте корректный TOML файл или /cancel для отмены\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return WAITING_FOR_CONFIG
        except Exception as e:
            await update.message.reply_text(
                f"❌ Ошибка чтения файла: {MessageTemplates.escape_markdown(str(e))}",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return WAITING_FOR_CONFIG
    
    async def _process_config(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE,
        config: Dict[str, Any]
    ) -> int:
        """
        Process and save configuration.
        """
        chat = update.effective_chat
        user = update.effective_user
        
        # Validate configuration
        errors = self._validate_config(config)
        if errors:
            error_list = "\n".join([f"• {MessageTemplates.escape_markdown(e)}" for e in errors])
            await update.message.reply_text(
                f"❌ *Ошибки в конфигурации:*\n\n{error_list}\n\n"
                "Исправьте ошибки и отправьте снова или /cancel для отмены\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return WAITING_FOR_CONFIG
        
        # Get existing locations
        existing_locations = await self.db.get_locations_by_chat(chat.id, active_only=False)
        existing_by_name = {loc.name: loc for loc in existing_locations}
        
        # Get new location names from config
        locations_config = config.get("locations", [])
        new_names = {loc.get("name", "") for loc in locations_config}
        
        # Find locations to delete (exist in DB but not in new config)
        locations_to_delete = [
            loc for loc in existing_locations 
            if loc.name not in new_names
        ]
        
        if locations_to_delete:
            # Store pending config in context for later processing
            context.user_data["pending_config"] = config
            context.user_data["locations_to_delete"] = [loc.id for loc in locations_to_delete]
            
            # Ask for confirmation
            delete_names = "\n".join([f"• {loc.name}" for loc in locations_to_delete])
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Да, удалить", callback_data=f"{CONFIRM_DELETE_PREFIX}{chat.id}"),
                    InlineKeyboardButton("❌ Нет, отменить", callback_data=f"{CANCEL_DELETE_PREFIX}{chat.id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"⚠️ *Следующие локации будут удалены:*\n\n{MessageTemplates.escape_markdown(delete_names)}\n\n"
                "Подтвердите удаление:",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup
            )
            
            return WAITING_FOR_DELETE_CONFIRM
        
        # No deletions needed, proceed with save
        return await self._save_config(update, context, config, existing_by_name)
    
    async def handle_delete_confirmation(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle delete confirmation callback."""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data.startswith(CONFIRM_DELETE_PREFIX):
            # User confirmed deletion
            config = context.user_data.get("pending_config")
            locations_to_delete = context.user_data.get("locations_to_delete", [])
            
            if not config:
                await query.edit_message_text(
                    "❌ Ошибка: конфигурация не найдена\\. Начните заново с /set\\_config",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return ConversationHandler.END
            
            # Delete locations (hard delete since user confirmed)
            deleted_count = 0
            for loc_id in locations_to_delete:
                await self.db.delete_location(loc_id, hard_delete=True)
                deleted_count += 1
            
            # Get existing locations (after deletion)
            chat = update.effective_chat
            existing_locations = await self.db.get_locations_by_chat(chat.id, active_only=False)
            existing_by_name = {loc.name: loc for loc in existing_locations}
            
            # Save the rest of the config
            result = await self._save_config(update, context, config, existing_by_name, deleted_count)
            
            # Clean up
            context.user_data.pop("pending_config", None)
            context.user_data.pop("locations_to_delete", None)
            
            return result
        
        elif data.startswith(CANCEL_DELETE_PREFIX):
            # User cancelled
            context.user_data.pop("pending_config", None)
            context.user_data.pop("locations_to_delete", None)
            
            await query.edit_message_text(
                "❌ Изменение конфигурации отменено\\.\n\n"
                "Локации не были удалены\\. Используйте /set\\_config чтобы начать заново\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return ConversationHandler.END
        
        return ConversationHandler.END
    
    async def _save_config(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        config: Dict[str, Any],
        existing_by_name: Dict[str, Location],
        deleted_count: int = 0
    ) -> int:
        """Save configuration to database."""
        # Determine chat and user from update
        if update.callback_query:
            chat = update.effective_chat
            user = update.effective_user
            reply_func = update.callback_query.edit_message_text
        else:
            chat = update.effective_chat
            user = update.effective_user
            reply_func = update.message.reply_text
        
        try:
            # Update chat settings
            settings = await self.db.get_or_create_chat_settings(
                chat_id=chat.id,
                chat_type=chat.type,
                chat_title=chat.title or user.first_name
            )
            
            # Update templates if provided
            templates = config.get("templates", {})
            if templates.get("flyable"):
                settings.flyable_template = templates["flyable"]
            if templates.get("not_flyable"):
                settings.not_flyable_template = templates["not_flyable"]
            
            if "notifications_enabled" in config:
                settings.notifications_enabled = config["notifications_enabled"]
            
            await self.db.update_chat_settings(settings)
            
            # Process locations
            locations_config = config.get("locations", [])
            created_count = 0
            updated_count = 0
            
            for loc_config in locations_config:
                name = loc_config.get("name", "")
                
                if name in existing_by_name:
                    # Update existing location
                    location = existing_by_name[name]
                    self._update_location_from_config(location, loc_config)
                    await self.db.update_location(location)
                    updated_count += 1
                else:
                    # Create new location
                    location = self._create_location_from_config(chat.id, loc_config)
                    await self.db.create_location(location)
                    created_count += 1
            
            # Add user as admin if not already
            await self.db.add_admin_user(chat.id, user.id, user.username)
            
            # Build result message
            result_parts = []
            if created_count > 0:
                result_parts.append(f"📍 Создано: {created_count}")
            if updated_count > 0:
                result_parts.append(f"📝 Обновлено: {updated_count}")
            if deleted_count > 0:
                result_parts.append(f"🗑 Удалено: {deleted_count}")
            
            result_text = "\n".join(result_parts) if result_parts else "Без изменений"
            
            await reply_func(
                f"✅ *Конфигурация сохранена\\!*\n\n{MessageTemplates.escape_markdown(result_text)}\n\n"
                f"Используйте /list\\_locations для просмотра\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            logger.info(
                f"Config saved for chat {chat.id} by user {user.id}: "
                f"{created_count} created, {updated_count} updated, {deleted_count} deleted"
            )
            
            return ConversationHandler.END
        
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            await reply_func(
                f"❌ Ошибка сохранения: {MessageTemplates.escape_markdown(str(e))}",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return ConversationHandler.END
    
    def _validate_config(self, config: Dict[str, Any]) -> List[str]:
        """
        Validate configuration and return list of errors.
        """
        errors = []
        
        if not isinstance(config, dict):
            errors.append("Конфигурация должна быть объектом TOML")
            return errors
        
        locations = config.get("locations", [])
        
        if not locations:
            errors.append("Требуется хотя бы одна локация в [[locations]]")
            return errors
        
        for i, loc in enumerate(locations):
            prefix = f"Локация {i + 1}"
            
            if not loc.get("name"):
                errors.append(f"{prefix}: отсутствует 'name'")
            
            lat = loc.get("latitude")
            if lat is None:
                errors.append(f"{prefix}: отсутствует 'latitude'")
            elif not (-90 <= lat <= 90):
                errors.append(f"{prefix}: 'latitude' должна быть от -90 до 90")
            
            lon = loc.get("longitude")
            if lon is None:
                errors.append(f"{prefix}: отсутствует 'longitude'")
            elif not (-180 <= lon <= 180):
                errors.append(f"{prefix}: 'longitude' должна быть от -180 до 180")
            
            # Validate time window
            start = loc.get("time_window_start", 8)
            end = loc.get("time_window_end", 18)
            if not (0 <= start <= 23):
                errors.append(f"{prefix}: 'time_window_start' должен быть 0-23")
            if not (0 <= end <= 23):
                errors.append(f"{prefix}: 'time_window_end' должен быть 0-23")
            if start >= end:
                errors.append(f"{prefix}: 'time_window_start' должен быть меньше 'time_window_end'")
            
            # Validate temperature
            temp_min = loc.get("temp_min", 5)
            temp_max = loc.get("temp_max", 35)
            if temp_min >= temp_max:
                errors.append(f"{prefix}: 'temp_min' должен быть меньше 'temp_max'")
            
            # Validate wind directions
            wind_dirs = loc.get("wind_directions", [])
            if wind_dirs:
                if not isinstance(wind_dirs, list):
                    errors.append(f"{prefix}: 'wind_directions' должен быть массивом")
                else:
                    for d in wind_dirs:
                        if not (0 <= d <= 360):
                            errors.append(f"{prefix}: направление ветра должно быть 0-360")
                            break
        
        return errors
    
    def _create_location_from_config(
        self, 
        chat_id: int, 
        config: Dict[str, Any]
    ) -> Location:
        """Create a Location object from configuration."""
        wind_directions = config.get("wind_directions", [])
        
        return Location(
            chat_id=chat_id,
            name=config["name"],
            latitude=config["latitude"],
            longitude=config["longitude"],
            time_window_start=config.get("time_window_start", 8),
            time_window_end=config.get("time_window_end", 18),
            temp_min=config.get("temp_min", 5.0),
            temp_max=config.get("temp_max", 35.0),
            humidity_max=config.get("humidity_max", 85.0),
            wind_speed_max=config.get("wind_speed_max", 8.0),
            wind_directions=json.dumps(wind_directions),
            wind_direction_tolerance=config.get("wind_direction_tolerance", 45),
            dew_point_spread_min=config.get("dew_point_spread_min", 2.0),
            required_conditions_duration_hours=config.get("required_conditions_duration_hours", 4),
            precipitation_probability_max=config.get("precipitation_probability_max", 20.0),
            cloud_cover_max=config.get("cloud_cover_max", 80.0),
            is_active=config.get("is_active", True)
        )
    
    def _update_location_from_config(
        self, 
        location: Location, 
        config: Dict[str, Any]
    ) -> None:
        """Update a Location object from configuration."""
        location.latitude = config.get("latitude", location.latitude)
        location.longitude = config.get("longitude", location.longitude)
        location.time_window_start = config.get("time_window_start", location.time_window_start)
        location.time_window_end = config.get("time_window_end", location.time_window_end)
        location.temp_min = config.get("temp_min", location.temp_min)
        location.temp_max = config.get("temp_max", location.temp_max)
        location.humidity_max = config.get("humidity_max", location.humidity_max)
        location.wind_speed_max = config.get("wind_speed_max", location.wind_speed_max)
        
        wind_directions = config.get("wind_directions")
        if wind_directions is not None:
            location.wind_directions = json.dumps(wind_directions)
        
        location.wind_direction_tolerance = config.get(
            "wind_direction_tolerance", location.wind_direction_tolerance
        )
        location.dew_point_spread_min = config.get(
            "dew_point_spread_min", location.dew_point_spread_min
        )
        location.required_conditions_duration_hours = config.get(
            "required_conditions_duration_hours", location.required_conditions_duration_hours
        )
        location.precipitation_probability_max = config.get(
            "precipitation_probability_max", location.precipitation_probability_max
        )
        location.cloud_cover_max = config.get("cloud_cover_max", location.cloud_cover_max)
        
        if "is_active" in config:
            location.is_active = config["is_active"]
    
    async def cancel_config(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Cancel configuration process."""
        # Clean up any pending data
        context.user_data.pop("pending_config", None)
        context.user_data.pop("locations_to_delete", None)
        
        await update.message.reply_text(
            "❌ Настройка отменена\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END
