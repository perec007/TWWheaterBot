"""
Message templates for weather notifications.
Uses MarkdownV2 format for Telegram.
"""

import re
from datetime import datetime
from typing import Optional
import pytz

from ..weather.analyzer import AnalysisResult
from ..database.models import Location, ChatSettings


class MessageTemplates:
    """
    Message template formatter for Telegram notifications.
    
    All templates use MarkdownV2 format which requires escaping special characters.
    """
    
    # Characters that need to be escaped in MarkdownV2
    ESCAPE_CHARS = r'_*[]()~`>#+-=|{}.!'
    
    @classmethod
    def escape_markdown(cls, text: str) -> str:
        """
        Escape special characters for MarkdownV2.
        
        Args:
            text: Raw text to escape
        
        Returns:
            Escaped text safe for MarkdownV2
        """
        if not text:
            return ""
        return re.sub(r'([_*\[\]()~`>#+=|{}.!-])', r'\\\1', str(text))
    
    @classmethod
    def format_flyable_message(
        cls,
        result: AnalysisResult,
        location: Location,
        template: Optional[str] = None,
        timezone: pytz.timezone = pytz.UTC
    ) -> str:
        """
        Format a "flyable weather" notification message.
        
        Args:
            result: Weather analysis result
            location: Location data
            template: Custom template or None for default
            timezone: Timezone for timestamps
        
        Returns:
            Formatted MarkdownV2 message
        """
        now = datetime.now(timezone)
        
        # Build flyable window string
        flyable_window = "—"
        if result.flyable_window_start and result.flyable_window_end:
            flyable_window = f"{result.flyable_window_start} — {result.flyable_window_end}"
        
        # Build temperature range from location settings
        temp_range = f"{location.temp_min}°C — {location.temp_max}°C"
        
        # Build wind info
        wind_info = "—"
        if result.current_wind_speed is not None:
            wind_dir_name = cls._get_wind_direction_name(result.current_wind_direction or 0)
            wind_info = f"{result.current_wind_speed:.1f} м/с, {wind_dir_name}"
        
        # Format values
        values = {
            "location_name": cls.escape_markdown(result.location_name),
            "date": cls.escape_markdown(result.date),
            "flyable_window": cls.escape_markdown(flyable_window),
            "temp_range": cls.escape_markdown(temp_range),
            "wind_info": cls.escape_markdown(wind_info),
            "humidity": cls.escape_markdown(str(int(result.current_humidity or 0))),
            "cloud_cover": cls.escape_markdown(str(int(result.current_cloud_cover or 0))),
            "updated_at": cls.escape_markdown(now.strftime("%H:%M %d.%m.%Y")),
            "continuous_hours": cls.escape_markdown(str(result.continuous_hours)),
        }
        
        if template:
            try:
                return template.format(**values)
            except KeyError:
                pass
        
        # Default template
        return f"""✅🪂 *ЛЁТНАЯ ПОГОДА\\!*

📍 *Локация:* {values['location_name']}
📅 *Дата:* {values['date']}
⏰ *Лётное окно:* {values['flyable_window']} \\({values['continuous_hours']} ч\\.\\)

*Условия:*
🌡 Температура: {values['temp_range']}
💨 Ветер: {values['wind_info']}
💧 Влажность: до {cls.escape_markdown(str(location.humidity_max))}%
🌤 Облачность: до {cls.escape_markdown(str(location.cloud_cover_max))}%

_Данные подтверждены двумя источниками_
_Обновлено: {values['updated_at']}_"""
    
    @classmethod
    def format_not_flyable_message(
        cls,
        result: AnalysisResult,
        location: Location,
        template: Optional[str] = None,
        timezone: pytz.timezone = pytz.UTC
    ) -> str:
        """
        Format a "not flyable weather" notification message.
        
        Args:
            result: Weather analysis result
            location: Location data
            template: Custom template or None for default
            timezone: Timezone for timestamps
        
        Returns:
            Formatted MarkdownV2 message
        """
        now = datetime.now(timezone)
        
        # Build rejection reasons list
        reasons_list = []
        for reason in result.rejection_reasons:
            escaped_reason = cls.escape_markdown(reason)
            reasons_list.append(f"• {escaped_reason}")
        
        rejection_reasons = "\n".join(reasons_list) if reasons_list else "• Условия не соответствуют критериям"
        
        # Wind direction
        wind_direction = "—"
        if result.current_wind_direction is not None:
            wind_direction = str(result.current_wind_direction)
        
        # Format values
        values = {
            "location_name": cls.escape_markdown(result.location_name),
            "date": cls.escape_markdown(result.date),
            "rejection_reasons": rejection_reasons,
            "temp": cls.escape_markdown(f"{result.current_temp:.1f}" if result.current_temp else "—"),
            "wind_speed": cls.escape_markdown(f"{result.current_wind_speed:.1f}" if result.current_wind_speed else "—"),
            "wind_direction": cls.escape_markdown(wind_direction),
            "humidity": cls.escape_markdown(str(int(result.current_humidity or 0))),
            "cloud_cover": cls.escape_markdown(str(int(result.current_cloud_cover or 0))),
            "updated_at": cls.escape_markdown(now.strftime("%H:%M %d.%m.%Y")),
        }
        
        if template:
            try:
                return template.format(**values)
            except KeyError:
                pass
        
        # Default template
        return f"""❌🌧️ *СТАЛО НЕ ЛЁТНО*

📍 *Локация:* {values['location_name']}
📅 *Дата:* {values['date']}

*Причины отмены:*
{values['rejection_reasons']}

*Текущие условия:*
🌡 Температура: {values['temp']}°C
💨 Ветер: {values['wind_speed']} м/с, {values['wind_direction']}°
💧 Влажность: {values['humidity']}%
🌤 Облачность: {values['cloud_cover']}%

_Обновлено: {values['updated_at']}_"""
    
    @classmethod
    def format_status_message(
        cls,
        result: AnalysisResult,
        location: Location,
        timezone: pytz.timezone = pytz.UTC
    ) -> str:
        """
        Format a status check message (not a notification).
        
        Args:
            result: Weather analysis result
            location: Location data
            timezone: Timezone for timestamps
        
        Returns:
            Formatted MarkdownV2 message
        """
        now = datetime.now(timezone)
        
        status_emoji = "✅🪂" if result.is_flyable else "❌"
        status_text = "ЛЁТНО" if result.is_flyable else "НЕ ЛЁТНО"
        
        # Build flyable hours list
        flyable_hours_str = "—"
        if result.flyable_hours:
            hours = [f"{h:02d}:00" for h in result.flyable_hours]
            flyable_hours_str = ", ".join(hours)
        
        # Wind direction name
        wind_dir_name = "—"
        if result.current_wind_direction is not None:
            wind_dir_name = cls._get_wind_direction_name(result.current_wind_direction)
        
        message = f"""{status_emoji} *Статус: {cls.escape_markdown(status_text)}*

📍 *Локация:* {cls.escape_markdown(result.location_name)}
📅 *Дата:* {cls.escape_markdown(result.date)}

*Текущая погода:*
🌡 Температура: {cls.escape_markdown(f'{result.current_temp:.1f}' if result.current_temp else '—')}°C
💨 Ветер: {cls.escape_markdown(f'{result.current_wind_speed:.1f}' if result.current_wind_speed else '—')} м/с, {cls.escape_markdown(wind_dir_name)}
💧 Влажность: {cls.escape_markdown(str(int(result.current_humidity or 0)))}%
🌤 Облачность: {cls.escape_markdown(str(int(result.current_cloud_cover or 0)))}%

*Лётные часы:* {cls.escape_markdown(flyable_hours_str)}
*Требуется непрерывно:* {location.required_conditions_duration_hours} ч\\.
"""
        
        if result.is_flyable:
            message += f"""
*Лётное окно:* {cls.escape_markdown(result.flyable_window_start or '—')} — {cls.escape_markdown(result.flyable_window_end or '—')}
"""
        else:
            reasons = "\n".join([f"• {cls.escape_markdown(r)}" for r in result.rejection_reasons])
            message += f"""
*Причины:*
{reasons}
"""
        
        message += f"""
_OpenWeather: {'✅' if result.openweather_available else '❌'} \\| VisualCrossing: {'✅' if result.visualcrossing_available else '❌'}_
_Обновлено: {cls.escape_markdown(now.strftime('%H:%M %d.%m.%Y'))}_"""
        
        return message
    
    @classmethod
    def format_location_list(
        cls,
        locations: list,
        chat_title: Optional[str] = None
    ) -> str:
        """
        Format a list of locations.
        
        Args:
            locations: List of Location objects
            chat_title: Optional chat title
        
        Returns:
            Formatted MarkdownV2 message
        """
        if not locations:
            return "📍 *Нет настроенных локаций*\n\nИспользуйте /set\\_config для добавления локаций\\."
        
        title = f"📍 *Локации"
        if chat_title:
            title += f" для {cls.escape_markdown(chat_title)}"
        title += "*\n\n"
        
        lines = [title]
        
        for i, loc in enumerate(locations, 1):
            status = "✅" if loc.is_active else "⏸"
            coords = f"{loc.latitude:.4f}, {loc.longitude:.4f}"
            
            lines.append(f"{status} *{i}\\. {cls.escape_markdown(loc.name)}*")
            lines.append(f"   📌 Координаты: `{coords}`")
            lines.append(f"   ⏰ Окно: {loc.time_window_start:02d}:00 \\- {loc.time_window_end:02d}:00")
            lines.append(f"   💨 Макс\\. ветер: {cls.escape_markdown(str(loc.wind_speed_max))} м/с")
            lines.append(f"   🌡 Температура: {cls.escape_markdown(str(loc.temp_min))}°C \\- {cls.escape_markdown(str(loc.temp_max))}°C")
            lines.append("")
        
        return "\n".join(lines)
    
    @classmethod
    def format_config_message(
        cls,
        location: Location
    ) -> str:
        """
        Format location configuration for display.
        
        Args:
            location: Location configuration
        
        Returns:
            Formatted MarkdownV2 message
        """
        wind_dirs = location.get_wind_directions_list()
        wind_dirs_str = ", ".join([f"{d}°" for d in wind_dirs]) if wind_dirs else "все"
        
        return f"""⚙️ *Конфигурация: {cls.escape_markdown(location.name)}*

*Координаты:*
📌 Широта: `{location.latitude}`
📌 Долгота: `{location.longitude}`

*Временное окно:*
⏰ Начало: {location.time_window_start:02d}:00
⏰ Конец: {location.time_window_end:02d}:00
⏱ Мин\\. непрерывно: {location.required_conditions_duration_hours} ч\\.

*Температура:*
🌡 Минимум: {cls.escape_markdown(str(location.temp_min))}°C
🌡 Максимум: {cls.escape_markdown(str(location.temp_max))}°C

*Влажность:*
💧 Максимум: {cls.escape_markdown(str(location.humidity_max))}%

*Ветер:*
💨 Макс\\. скорость: {cls.escape_markdown(str(location.wind_speed_max))} м/с
🧭 Направления: {cls.escape_markdown(wind_dirs_str)}
🎯 Допуск: ±{location.wind_direction_tolerance}°

*Дополнительно:*
🌫 Мин\\. разница с точкой росы: {cls.escape_markdown(str(location.dew_point_spread_min))}°C
🌧 Макс\\. вероятность осадков: {cls.escape_markdown(str(location.precipitation_probability_max))}%
☁️ Макс\\. облачность: {cls.escape_markdown(str(location.cloud_cover_max))}%

*Статус:* {'✅ Активна' if location.is_active else '⏸ Приостановлена'}"""
    
    @classmethod
    def format_current_weather(
        cls,
        location,
        weather_data: dict,
        timezone = None
    ) -> str:
        """
        Format current weather data for display.
        
        Args:
            location: Location object
            weather_data: Dictionary with weather data
            timezone: Timezone for timestamps
        
        Returns:
            Formatted MarkdownV2 message
        """
        from datetime import datetime
        import pytz
        
        if timezone is None:
            timezone = pytz.UTC
        
        now = datetime.now(timezone)
        
        # Get values with defaults
        temp = weather_data.get("temperature")
        feels_like = weather_data.get("feels_like")
        humidity = weather_data.get("humidity")
        wind_speed = weather_data.get("wind_speed")
        wind_gust = weather_data.get("wind_gust")
        wind_dir = weather_data.get("wind_direction")
        cloud_cover = weather_data.get("cloud_cover")
        pressure = weather_data.get("pressure")
        visibility = weather_data.get("visibility")
        dew_point = weather_data.get("dew_point")
        condition = weather_data.get("weather_description") or weather_data.get("weather_condition", "")
        sources = weather_data.get("sources", [])
        
        # Wind direction name
        wind_dir_name = cls._get_wind_direction_name(int(wind_dir)) if wind_dir is not None else "—"
        
        # Calculate dew point spread
        dew_spread = None
        if temp is not None and dew_point is not None:
            dew_spread = temp - dew_point
        
        # Determine weather emoji
        weather_emoji = "🌤"
        if condition:
            condition_lower = condition.lower()
            if "rain" in condition_lower or "дождь" in condition_lower:
                weather_emoji = "🌧"
            elif "snow" in condition_lower or "снег" in condition_lower:
                weather_emoji = "🌨"
            elif "cloud" in condition_lower or "облач" in condition_lower:
                weather_emoji = "☁️"
            elif "clear" in condition_lower or "ясно" in condition_lower:
                weather_emoji = "☀️"
            elif "thunder" in condition_lower or "гроз" in condition_lower:
                weather_emoji = "⛈"
            elif "fog" in condition_lower or "туман" in condition_lower:
                weather_emoji = "🌫"
        
        # Format values
        temp_str = f"{temp:.1f}" if temp is not None else "—"
        feels_str = f"{feels_like:.1f}" if feels_like is not None else "—"
        humidity_str = str(int(humidity)) if humidity is not None else "—"
        wind_str = f"{wind_speed:.1f}" if wind_speed is not None else "—"
        gust_str = f"{wind_gust:.1f}" if wind_gust is not None else None
        wind_dir_str = str(int(wind_dir)) if wind_dir is not None else "—"
        cloud_str = str(int(cloud_cover)) if cloud_cover is not None else "—"
        pressure_str = str(int(pressure)) if pressure is not None else "—"
        visibility_str = f"{visibility:.1f}" if visibility is not None else "—"
        dew_point_str = f"{dew_point:.1f}" if dew_point is not None else "—"
        dew_spread_str = f"{dew_spread:.1f}" if dew_spread is not None else "—"
        
        # Build message
        message = f"""{weather_emoji} *Текущая погода: {cls.escape_markdown(location.name)}*

🌡 *Температура:* {cls.escape_markdown(temp_str)}°C
🤒 *Ощущается:* {cls.escape_markdown(feels_str)}°C

💨 *Ветер:* {cls.escape_markdown(wind_str)} м/с, {cls.escape_markdown(wind_dir_name)} \\({cls.escape_markdown(wind_dir_str)}°\\)"""

        if gust_str:
            message += f"\n🌬 *Порывы:* {cls.escape_markdown(gust_str)} м/с"

        message += f"""

💧 *Влажность:* {cls.escape_markdown(humidity_str)}%
🌫 *Точка росы:* {cls.escape_markdown(dew_point_str)}°C \\(разница: {cls.escape_markdown(dew_spread_str)}°C\\)
☁️ *Облачность:* {cls.escape_markdown(cloud_str)}%
🔭 *Видимость:* {cls.escape_markdown(visibility_str)} км
🌡 *Давление:* {cls.escape_markdown(pressure_str)} гПа"""

        if condition:
            message += f"\n\n📋 *Условия:* {cls.escape_markdown(condition)}"

        sources_str = ", ".join(sources) if sources else "—"
        message += f"""

_Источники: {cls.escape_markdown(sources_str)}_
_Обновлено: {cls.escape_markdown(now.strftime('%H:%M %d.%m.%Y'))}_"""

        return message
    
    # Example TOML configuration
    EXAMPLE_CONFIG = """# Настройки уведомлений
notifications_enabled = true

[[locations]]
name = "Юца"
latitude = 43.9234
longitude = 42.7345
time_window_start = 8
time_window_end = 18
temp_min = 5
temp_max = 35
humidity_max = 85
wind_speed_max = 8
wind_directions = [0, 45, 315]
wind_direction_tolerance = 45
dew_point_spread_min = 2
required_conditions_duration_hours = 4
precipitation_probability_max = 20
cloud_cover_max = 80"""
    
    @classmethod
    def format_help_message(cls) -> str:
        """Format the help message."""
        return f"""🪂 *Бот мониторинга погоды для парапланеристов*

*Доступные команды:*

/start — Приветствие и начало работы
/help — Показать это сообщение
/weather — Текущая погода \\(или /weather Юца\\)
/list\\_locations — Список настроенных локаций
/status — Статус лётной погоды
/check — Запустить проверку погоды
/get\\_config — Получить текущие настройки
/set\\_config — Изменить конфигурацию

*Как это работает:*
1\\. Бот периодически проверяет погоду из двух источников
2\\. При наступлении лётных условий — отправляет уведомление
3\\. При изменении на нелётные — предупреждает с указанием причин

\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_

*Пример конфигурации \\(TOML\\):*

```toml
{cls.escape_markdown(cls.EXAMPLE_CONFIG)}
```

*Параметры локации:*
• `name` — название локации
• `latitude` — широта \\(напр\\. 43\\.9234\\)
• `longitude` — долгота \\(напр\\. 42\\.7345\\)
• `time_window_start` — начало окна \\(0\\-23\\)
• `time_window_end` — конец окна \\(0\\-23\\)
• `temp_min` / `temp_max` — температура °C
• `humidity_max` — макс\\. влажность %
• `wind_speed_max` — макс\\. ветер м/с
• `wind_directions` — направления \\[градусы\\]
• `wind_direction_tolerance` — допуск ±°
• `dew_point_spread_min` — разница с точкой росы
• `required_conditions_duration_hours` — мин\\. часов
• `precipitation_probability_max` — осадки %
• `cloud_cover_max` — облачность %

_Данные от OpenWeather и VisualCrossing_"""
    
    @classmethod
    def format_welcome_message(cls, user_name: str) -> str:
        """Format the welcome message."""
        return f"""👋 *Привет, {cls.escape_markdown(user_name)}\\!*

Я — бот мониторинга погоды для парапланеристов 🪂

Я слежу за погодой и оповещаю, когда условия становятся подходящими для полётов\\.

*Начните с:*
• /list\\_locations — посмотреть локации
• /status — текущий статус погоды
• /help — все команды

_Используйте /set\\_config для настройки локаций_"""
    
    @staticmethod
    def _get_wind_direction_name(degrees: int) -> str:
        """Convert wind direction in degrees to compass name."""
        directions = [
            "С", "ССВ", "СВ", "ВСВ",
            "В", "ВЮВ", "ЮВ", "ЮЮВ",
            "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ",
            "З", "ЗСЗ", "СЗ", "ССЗ"
        ]
        idx = round(degrees / 22.5) % 16
        return directions[idx]
