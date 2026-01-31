"""
Database operations for the Weather Bot.
Uses SQLite with async support via aiosqlite.
"""

import aiosqlite
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from .models import Location, ChatSettings, WeatherStatus, WeatherCheck, AdminUser, WeatherForecast, FlyableWindow

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database manager."""
    
    def __init__(self, db_path: str):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None
    
    async def connect(self) -> None:
        """Connect to the database and create tables if needed."""
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        
        await self._create_tables()
        logger.info(f"Connected to database: {self.db_path}")
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")
    
    async def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        async with self._connection.cursor() as cursor:
            # Locations table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    time_window_start INTEGER DEFAULT 8,
                    time_window_end INTEGER DEFAULT 18,
                    temp_min REAL DEFAULT 5.0,
                    humidity_max REAL DEFAULT 85.0,
                    wind_speed_max REAL DEFAULT 8.0,
                    wind_gust_max REAL DEFAULT 12.0,
                    wind_directions TEXT DEFAULT '[]',
                    wind_direction_tolerance INTEGER DEFAULT 45,
                    dew_point_spread_min REAL DEFAULT 2.0,
                    required_conditions_duration_hours INTEGER DEFAULT 4,
                    precipitation_probability_max REAL DEFAULT 20.0,
                    cloud_cover_max REAL DEFAULT 80.0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migration: drop temp_max if present (SQLite 3.35.0+)
            try:
                await cursor.execute("ALTER TABLE locations DROP COLUMN temp_max")
                await self._connection.commit()
                logger.info("Migration: dropped column locations.temp_max")
            except Exception as e:
                if "no such column" not in str(e).lower():
                    logger.debug("Migration temp_max: %s", e)
            
            # Migration: add wind_gust_max column
            try:
                await cursor.execute("ALTER TABLE locations ADD COLUMN wind_gust_max REAL DEFAULT 12.0")
                await self._connection.commit()
                logger.info("Migration: added column locations.wind_gust_max")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug("Migration wind_gust_max: %s", e)
            
            # Chat settings table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    chat_type TEXT DEFAULT 'private',
                    chat_title TEXT,
                    flyable_template TEXT,
                    not_flyable_template TEXT,
                    notifications_enabled INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Weather status table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS weather_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    is_flyable INTEGER DEFAULT 0,
                    flyable_window_start TEXT,
                    flyable_window_end TEXT,
                    active_windows_json TEXT DEFAULT '[]',
                    last_forecast_id INTEGER,
                    consecutive_not_flyable_checks INTEGER DEFAULT 0,
                    last_notification_type TEXT,
                    last_notification_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (location_id) REFERENCES locations(id),
                    UNIQUE(location_id, date)
                )
            """)
            
            # Weather forecasts table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS weather_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id INTEGER NOT NULL,
                    check_time TIMESTAMP NOT NULL,
                    forecast_start TIMESTAMP NOT NULL,
                    forecast_end TIMESTAMP NOT NULL,
                    openweather_data TEXT,
                    visualcrossing_data TEXT,
                    total_flyable_windows INTEGER DEFAULT 0,
                    flyable_windows_json TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (location_id) REFERENCES locations(id)
                )
            """)
            
            # Flyable windows table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS flyable_windows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id INTEGER NOT NULL,
                    forecast_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    start_hour INTEGER NOT NULL,
                    end_hour INTEGER NOT NULL,
                    duration_hours INTEGER NOT NULL,
                    avg_temp REAL,
                    avg_wind_speed REAL,
                    max_wind_speed REAL,
                    avg_humidity REAL,
                    max_precipitation_prob REAL,
                    notified INTEGER DEFAULT 0,
                    notified_at TIMESTAMP,
                    cancelled INTEGER DEFAULT 0,
                    cancelled_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (location_id) REFERENCES locations(id),
                    FOREIGN KEY (forecast_id) REFERENCES weather_forecasts(id)
                )
            """)
            
            # Weather checks table (for auditing)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS weather_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id INTEGER NOT NULL,
                    check_time TIMESTAMP NOT NULL,
                    openweather_data TEXT,
                    visualcrossing_data TEXT,
                    is_flyable INTEGER DEFAULT 0,
                    rejection_reasons TEXT DEFAULT '[]',
                    flyable_hours TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (location_id) REFERENCES locations(id)
                )
            """)
            
            # Admin users table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, user_id)
                )
            """)
            
            # Create indexes for faster queries
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_locations_chat_id 
                ON locations(chat_id)
            """)
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_status_location_date 
                ON weather_status(location_id, date)
            """)
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_checks_location_time 
                ON weather_checks(location_id, check_time)
            """)
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_admin_users_chat_id 
                ON admin_users(chat_id)
            """)
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_forecasts_location_time 
                ON weather_forecasts(location_id, check_time)
            """)
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_flyable_windows_location_date 
                ON flyable_windows(location_id, date)
            """)
            await cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_flyable_windows_forecast 
                ON flyable_windows(forecast_id)
            """)
            
            # Migration: Add new columns to existing tables
            try:
                await cursor.execute("ALTER TABLE weather_status ADD COLUMN active_windows_json TEXT DEFAULT '[]'")
            except:
                pass  # Column already exists
            try:
                await cursor.execute("ALTER TABLE weather_status ADD COLUMN last_forecast_id INTEGER")
            except:
                pass  # Column already exists
            try:
                await cursor.execute("ALTER TABLE flyable_windows ADD COLUMN source TEXT DEFAULT 'both'")
            except:
                pass  # Column already exists
            
            await self._connection.commit()
    
    # =========================================================================
    # Location operations
    # =========================================================================
    
    async def create_location(self, location: Location) -> Location:
        """Create a new location."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO locations (
                    chat_id, name, latitude, longitude,
                    time_window_start, time_window_end,
                    temp_min, humidity_max,
                    wind_speed_max, wind_gust_max, wind_directions, wind_direction_tolerance,
                    dew_point_spread_min, required_conditions_duration_hours,
                    precipitation_probability_max, cloud_cover_max,
                    is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                location.chat_id, location.name, location.latitude, location.longitude,
                location.time_window_start, location.time_window_end,
                location.temp_min, location.humidity_max,
                location.wind_speed_max, location.wind_gust_max, location.wind_directions, location.wind_direction_tolerance,
                location.dew_point_spread_min, location.required_conditions_duration_hours,
                location.precipitation_probability_max, location.cloud_cover_max,
                1 if location.is_active else 0
            ))
            await self._connection.commit()
            location.id = cursor.lastrowid
            logger.info(f"Created location: {location.name} (id={location.id})")
            return location
    
    async def get_location(self, location_id: int) -> Optional[Location]:
        """Get a location by ID."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM locations WHERE id = ?",
                (location_id,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_location(row)
            return None
    
    async def get_locations_by_chat(self, chat_id: int, active_only: bool = True) -> List[Location]:
        """Get all locations for a chat."""
        async with self._connection.cursor() as cursor:
            if active_only:
                await cursor.execute(
                    "SELECT * FROM locations WHERE chat_id = ? AND is_active = 1 ORDER BY name",
                    (chat_id,)
                )
            else:
                await cursor.execute(
                    "SELECT * FROM locations WHERE chat_id = ? ORDER BY name",
                    (chat_id,)
                )
            rows = await cursor.fetchall()
            return [self._row_to_location(row) for row in rows]
    
    async def get_all_active_locations(self) -> List[Location]:
        """Get all active locations from all chats."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM locations WHERE is_active = 1 ORDER BY chat_id, name"
            )
            rows = await cursor.fetchall()
            return [self._row_to_location(row) for row in rows]
    
    async def update_location(self, location: Location) -> None:
        """Update an existing location."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                UPDATE locations SET
                    name = ?, latitude = ?, longitude = ?,
                    time_window_start = ?, time_window_end = ?,
                    temp_min = ?, humidity_max = ?,
                    wind_speed_max = ?, wind_gust_max = ?, wind_directions = ?, wind_direction_tolerance = ?,
                    dew_point_spread_min = ?, required_conditions_duration_hours = ?,
                    precipitation_probability_max = ?, cloud_cover_max = ?,
                    is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                location.name, location.latitude, location.longitude,
                location.time_window_start, location.time_window_end,
                location.temp_min, location.humidity_max,
                location.wind_speed_max, location.wind_gust_max, location.wind_directions, location.wind_direction_tolerance,
                location.dew_point_spread_min, location.required_conditions_duration_hours,
                location.precipitation_probability_max, location.cloud_cover_max,
                1 if location.is_active else 0,
                location.id
            ))
            await self._connection.commit()
            logger.info(f"Updated location: {location.name} (id={location.id})")
    
    async def delete_location(self, location_id: int, hard_delete: bool = False) -> None:
        """
        Delete a location.
        
        Args:
            location_id: ID of the location to delete
            hard_delete: If True, permanently remove from DB. If False, soft delete (set is_active=0)
        """
        async with self._connection.cursor() as cursor:
            if hard_delete:
                # Delete in order: flyable_windows (via forecast_id) -> weather_forecasts -> status/checks -> location
                await cursor.execute(
                    "DELETE FROM flyable_windows WHERE location_id = ?",
                    (location_id,)
                )
                await cursor.execute(
                    "DELETE FROM weather_forecasts WHERE location_id = ?",
                    (location_id,)
                )
                await cursor.execute(
                    "DELETE FROM weather_status WHERE location_id = ?",
                    (location_id,)
                )
                await cursor.execute(
                    "DELETE FROM weather_checks WHERE location_id = ?",
                    (location_id,)
                )
                await cursor.execute(
                    "DELETE FROM locations WHERE id = ?",
                    (location_id,)
                )
                logger.info(f"Hard deleted location: id={location_id}")
            else:
                await cursor.execute(
                    "UPDATE locations SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (location_id,)
                )
                logger.info(f"Soft deleted location: id={location_id}")
            await self._connection.commit()
    
    def _row_to_location(self, row: aiosqlite.Row) -> Location:
        """Convert a database row to a Location object."""
        # Use dict() to allow .get() for optional/new columns
        row_dict = dict(row)
        return Location(
            id=row_dict["id"],
            chat_id=row_dict["chat_id"],
            name=row_dict["name"],
            latitude=row_dict["latitude"],
            longitude=row_dict["longitude"],
            time_window_start=row_dict["time_window_start"],
            time_window_end=row_dict["time_window_end"],
            temp_min=row_dict["temp_min"],
            humidity_max=row_dict["humidity_max"],
            wind_speed_max=row_dict["wind_speed_max"],
            wind_gust_max=row_dict.get("wind_gust_max", 12.0),  # Default for migration
            wind_directions=row_dict["wind_directions"],
            wind_direction_tolerance=row_dict["wind_direction_tolerance"],
            dew_point_spread_min=row_dict["dew_point_spread_min"],
            required_conditions_duration_hours=row_dict["required_conditions_duration_hours"],
            precipitation_probability_max=row_dict["precipitation_probability_max"],
            cloud_cover_max=row_dict["cloud_cover_max"],
            is_active=bool(row_dict["is_active"]),
            created_at=row_dict["created_at"],
            updated_at=row_dict["updated_at"]
        )
    
    # =========================================================================
    # Chat settings operations
    # =========================================================================
    
    async def get_or_create_chat_settings(self, chat_id: int, chat_type: str = "private", 
                                          chat_title: Optional[str] = None) -> ChatSettings:
        """Get chat settings or create default ones."""
        settings = await self.get_chat_settings(chat_id)
        if settings:
            return settings
        
        settings = ChatSettings(
            chat_id=chat_id,
            chat_type=chat_type,
            chat_title=chat_title
        )
        return await self.create_chat_settings(settings)
    
    async def get_chat_settings(self, chat_id: int) -> Optional[ChatSettings]:
        """Get chat settings by chat ID."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_chat_settings(row)
            return None
    
    async def create_chat_settings(self, settings: ChatSettings) -> ChatSettings:
        """Create new chat settings."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO chat_settings (
                    chat_id, chat_type, chat_title,
                    flyable_template, not_flyable_template,
                    notifications_enabled
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                settings.chat_id, settings.chat_type, settings.chat_title,
                settings.flyable_template, settings.not_flyable_template,
                1 if settings.notifications_enabled else 0
            ))
            await self._connection.commit()
            logger.info(f"Created chat settings for chat_id={settings.chat_id}")
            return settings
    
    async def update_chat_settings(self, settings: ChatSettings) -> None:
        """Update chat settings."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                UPDATE chat_settings SET
                    chat_type = ?, chat_title = ?,
                    flyable_template = ?, not_flyable_template = ?,
                    notifications_enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
            """, (
                settings.chat_type, settings.chat_title,
                settings.flyable_template, settings.not_flyable_template,
                1 if settings.notifications_enabled else 0,
                settings.chat_id
            ))
            await self._connection.commit()
            logger.info(f"Updated chat settings for chat_id={settings.chat_id}")
    
    def _row_to_chat_settings(self, row: aiosqlite.Row) -> ChatSettings:
        """Convert a database row to a ChatSettings object."""
        return ChatSettings(
            chat_id=row["chat_id"],
            chat_type=row["chat_type"],
            chat_title=row["chat_title"],
            flyable_template=row["flyable_template"] or ChatSettings.flyable_template,
            not_flyable_template=row["not_flyable_template"] or ChatSettings.not_flyable_template,
            notifications_enabled=bool(row["notifications_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )
    
    # =========================================================================
    # Weather status operations
    # =========================================================================
    
    async def get_weather_status(self, location_id: int, date: str) -> Optional[WeatherStatus]:
        """Get weather status for a location and date."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM weather_status WHERE location_id = ? AND date = ?",
                (location_id, date)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_weather_status(row)
            return None
    
    async def upsert_weather_status(self, status: WeatherStatus) -> WeatherStatus:
        """Insert or update weather status."""
        existing = await self.get_weather_status(status.location_id, status.date)
        
        async with self._connection.cursor() as cursor:
            if existing:
                await cursor.execute("""
                    UPDATE weather_status SET
                        is_flyable = ?,
                        flyable_window_start = ?,
                        flyable_window_end = ?,
                        active_windows_json = ?,
                        last_forecast_id = ?,
                        consecutive_not_flyable_checks = ?,
                        last_notification_type = ?,
                        last_notification_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE location_id = ? AND date = ?
                """, (
                    1 if status.is_flyable else 0,
                    status.flyable_window_start,
                    status.flyable_window_end,
                    status.active_windows_json,
                    status.last_forecast_id,
                    status.consecutive_not_flyable_checks,
                    status.last_notification_type,
                    status.last_notification_at,
                    status.location_id,
                    status.date
                ))
                status.id = existing.id
            else:
                await cursor.execute("""
                    INSERT INTO weather_status (
                        location_id, date, is_flyable,
                        flyable_window_start, flyable_window_end,
                        active_windows_json, last_forecast_id,
                        consecutive_not_flyable_checks,
                        last_notification_type, last_notification_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    status.location_id, status.date,
                    1 if status.is_flyable else 0,
                    status.flyable_window_start, status.flyable_window_end,
                    status.active_windows_json, status.last_forecast_id,
                    status.consecutive_not_flyable_checks,
                    status.last_notification_type, status.last_notification_at
                ))
                status.id = cursor.lastrowid
            
            await self._connection.commit()
            return status
    
    async def get_latest_weather_status(self, location_id: int) -> Optional[WeatherStatus]:
        """Get the most recent weather status for a location."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """SELECT * FROM weather_status 
                   WHERE location_id = ? 
                   ORDER BY date DESC, updated_at DESC 
                   LIMIT 1""",
                (location_id,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_weather_status(row)
            return None
    
    def _row_to_weather_status(self, row: aiosqlite.Row) -> WeatherStatus:
        """Convert a database row to a WeatherStatus object."""
        # Handle potentially missing new columns (migration safety)
        active_windows = "[]"
        last_forecast_id = None
        try:
            active_windows = row["active_windows_json"] or "[]"
            last_forecast_id = row["last_forecast_id"]
        except (KeyError, IndexError):
            pass
        
        return WeatherStatus(
            id=row["id"],
            location_id=row["location_id"],
            date=row["date"],
            is_flyable=bool(row["is_flyable"]),
            flyable_window_start=row["flyable_window_start"],
            flyable_window_end=row["flyable_window_end"],
            active_windows_json=active_windows,
            last_forecast_id=last_forecast_id,
            consecutive_not_flyable_checks=row["consecutive_not_flyable_checks"],
            last_notification_type=row["last_notification_type"],
            last_notification_at=row["last_notification_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )
    
    # =========================================================================
    # Weather check operations (for auditing)
    # =========================================================================
    
    async def create_weather_check(self, check: WeatherCheck) -> WeatherCheck:
        """Record a weather check."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO weather_checks (
                    location_id, check_time,
                    openweather_data, visualcrossing_data,
                    is_flyable, rejection_reasons, flyable_hours
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                check.location_id, check.check_time,
                check.openweather_data, check.visualcrossing_data,
                1 if check.is_flyable else 0,
                check.rejection_reasons, check.flyable_hours
            ))
            await self._connection.commit()
            check.id = cursor.lastrowid
            return check
    
    async def get_recent_weather_checks(self, location_id: int, limit: int = 10) -> List[WeatherCheck]:
        """Get recent weather checks for a location."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """SELECT * FROM weather_checks 
                   WHERE location_id = ? 
                   ORDER BY check_time DESC 
                   LIMIT ?""",
                (location_id, limit)
            )
            rows = await cursor.fetchall()
            return [self._row_to_weather_check(row) for row in rows]
    
    def _row_to_weather_check(self, row: aiosqlite.Row) -> WeatherCheck:
        """Convert a database row to a WeatherCheck object."""
        return WeatherCheck(
            id=row["id"],
            location_id=row["location_id"],
            check_time=row["check_time"],
            openweather_data=row["openweather_data"],
            visualcrossing_data=row["visualcrossing_data"],
            is_flyable=bool(row["is_flyable"]),
            rejection_reasons=row["rejection_reasons"],
            flyable_hours=row["flyable_hours"],
            created_at=row["created_at"]
        )
    
    # =========================================================================
    # Admin user operations
    # =========================================================================
    
    async def add_admin_user(self, chat_id: int, user_id: int, username: Optional[str] = None) -> None:
        """Add an admin user for a chat."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO admin_users (chat_id, user_id, username, added_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (chat_id, user_id, username))
            await self._connection.commit()
            logger.info(f"Added admin user {user_id} for chat {chat_id}")
    
    async def remove_admin_user(self, chat_id: int, user_id: int) -> None:
        """Remove an admin user from a chat."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM admin_users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id)
            )
            await self._connection.commit()
            logger.info(f"Removed admin user {user_id} from chat {chat_id}")
    
    async def get_admin_users(self, chat_id: int) -> List[AdminUser]:
        """Get all admin users for a chat."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM admin_users WHERE chat_id = ?",
                (chat_id,)
            )
            rows = await cursor.fetchall()
            return [
                AdminUser(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    user_id=row["user_id"],
                    username=row["username"],
                    added_at=row["added_at"]
                )
                for row in rows
            ]
    
    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        """Check if a user is an admin for a chat."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM admin_users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id)
            )
            return await cursor.fetchone() is not None
    
    # =========================================================================
    # Weather forecast operations
    # =========================================================================
    
    async def create_weather_forecast(self, forecast: WeatherForecast) -> WeatherForecast:
        """Create a new weather forecast record."""
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO weather_forecasts (
                    location_id, check_time, forecast_start, forecast_end,
                    openweather_data, visualcrossing_data,
                    total_flyable_windows, flyable_windows_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                forecast.location_id, forecast.check_time,
                forecast.forecast_start, forecast.forecast_end,
                forecast.openweather_data, forecast.visualcrossing_data,
                forecast.total_flyable_windows, forecast.flyable_windows_json
            ))
            await self._connection.commit()
            forecast.id = cursor.lastrowid
            logger.debug(f"Created weather forecast id={forecast.id} for location {forecast.location_id}")
            return forecast
    
    async def get_latest_forecast(self, location_id: int) -> Optional[WeatherForecast]:
        """Get the most recent forecast for a location."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """SELECT * FROM weather_forecasts 
                   WHERE location_id = ? 
                   ORDER BY check_time DESC 
                   LIMIT 1""",
                (location_id,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_weather_forecast(row)
            return None
    
    def _row_to_weather_forecast(self, row: aiosqlite.Row) -> WeatherForecast:
        """Convert a database row to a WeatherForecast object."""
        return WeatherForecast(
            id=row["id"],
            location_id=row["location_id"],
            check_time=row["check_time"],
            forecast_start=row["forecast_start"],
            forecast_end=row["forecast_end"],
            openweather_data=row["openweather_data"] or "{}",
            visualcrossing_data=row["visualcrossing_data"] or "{}",
            total_flyable_windows=row["total_flyable_windows"],
            flyable_windows_json=row["flyable_windows_json"] or "[]",
            created_at=row["created_at"]
        )
    
    # =========================================================================
    # Flyable window operations
    # =========================================================================
    
    async def create_flyable_window(self, window: FlyableWindow) -> FlyableWindow:
        """Create a new flyable window record."""
        source = getattr(window, "source", "both")
        async with self._connection.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO flyable_windows (
                    location_id, forecast_id, date, start_hour, end_hour, duration_hours,
                    source, avg_temp, avg_wind_speed, max_wind_speed, avg_humidity, max_precipitation_prob,
                    notified, notified_at, cancelled, cancelled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                window.location_id, window.forecast_id, window.date,
                window.start_hour, window.end_hour, window.duration_hours,
                source,
                window.avg_temp, window.avg_wind_speed, window.max_wind_speed,
                window.avg_humidity, window.max_precipitation_prob,
                1 if window.notified else 0, window.notified_at,
                1 if window.cancelled else 0, window.cancelled_at
            ))
            await self._connection.commit()
            window.id = cursor.lastrowid
            return window
    
    async def get_active_flyable_windows(self, location_id: int) -> List[FlyableWindow]:
        """Get all active (not cancelled) flyable windows for a location."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """SELECT * FROM flyable_windows 
                   WHERE location_id = ? AND cancelled = 0 AND date >= date('now')
                   ORDER BY date, start_hour""",
                (location_id,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_flyable_window(row) for row in rows]
    
    async def get_notified_windows(self, location_id: int) -> List[FlyableWindow]:
        """Get windows that have been notified about but not cancelled."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """SELECT * FROM flyable_windows 
                   WHERE location_id = ? AND notified = 1 AND cancelled = 0 AND date >= date('now')
                   ORDER BY date, start_hour""",
                (location_id,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_flyable_window(row) for row in rows]
    
    async def mark_window_notified(self, window_id: int, notified_at: datetime) -> None:
        """Mark a flyable window as notified."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE flyable_windows SET notified = 1, notified_at = ? WHERE id = ?",
                (notified_at, window_id)
            )
            await self._connection.commit()
    
    async def cancel_windows_not_in_forecast(
        self, 
        location_id: int, 
        current_windows: List[dict],
        cancelled_at: datetime
    ) -> List[FlyableWindow]:
        """
        Cancel windows that were previously notified but are no longer in the forecast.
        
        Args:
            location_id: Location ID
            current_windows: List of windows from current forecast {date, start_hour, end_hour}
            cancelled_at: Timestamp of cancellation
        
        Returns:
            List of cancelled windows
        """
        # Get all notified, non-cancelled windows
        notified_windows = await self.get_notified_windows(location_id)
        
        cancelled = []
        for window in notified_windows:
            # Check if this window still exists in current forecast
            still_exists = any(
                cw.get("date") == window.date and
                cw.get("start_hour") == window.start_hour and
                cw.get("end_hour") == window.end_hour and
                cw.get("source", "both") == getattr(window, "source", "both")
                for cw in current_windows
            )
            
            if not still_exists:
                # Window is no longer in forecast - cancel it
                async with self._connection.cursor() as cursor:
                    await cursor.execute(
                        "UPDATE flyable_windows SET cancelled = 1, cancelled_at = ? WHERE id = ?",
                        (cancelled_at, window.id)
                    )
                    await self._connection.commit()
                window.cancelled = True
                window.cancelled_at = cancelled_at
                cancelled.append(window)
        
        return cancelled
    
    def _row_to_flyable_window(self, row: aiosqlite.Row) -> FlyableWindow:
        """Convert a database row to a FlyableWindow object."""
        source = row["source"] if "source" in row.keys() else "both"
        return FlyableWindow(
            id=row["id"],
            location_id=row["location_id"],
            forecast_id=row["forecast_id"],
            date=row["date"],
            start_hour=row["start_hour"],
            end_hour=row["end_hour"],
            duration_hours=row["duration_hours"],
            source=source,
            avg_temp=row["avg_temp"],
            avg_wind_speed=row["avg_wind_speed"],
            max_wind_speed=row["max_wind_speed"],
            avg_humidity=row["avg_humidity"],
            max_precipitation_prob=row["max_precipitation_prob"],
            notified=bool(row["notified"]),
            notified_at=row["notified_at"],
            cancelled=bool(row["cancelled"]),
            cancelled_at=row["cancelled_at"],
            created_at=row["created_at"]
        )
    
    # =========================================================================
    # Utility operations
    # =========================================================================
    
    async def cleanup_old_checks(self, days_to_keep: int = 7) -> int:
        """Delete weather checks older than specified days."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """DELETE FROM weather_checks 
                   WHERE created_at < datetime('now', ?)""",
                (f'-{days_to_keep} days',)
            )
            deleted_checks = cursor.rowcount
            
            # Also cleanup old forecasts
            await cursor.execute(
                """DELETE FROM weather_forecasts 
                   WHERE created_at < datetime('now', ?)""",
                (f'-{days_to_keep} days',)
            )
            deleted_forecasts = cursor.rowcount
            
            # Cleanup old flyable windows (past dates)
            await cursor.execute(
                """DELETE FROM flyable_windows 
                   WHERE date < date('now', ?)""",
                (f'-{days_to_keep} days',)
            )
            deleted_windows = cursor.rowcount
            
            await self._connection.commit()
            total_deleted = deleted_checks + deleted_forecasts + deleted_windows
            if total_deleted > 0:
                logger.info(f"Cleaned up {deleted_checks} checks, {deleted_forecasts} forecasts, {deleted_windows} windows")
            return total_deleted
