"""
Database operations for the Weather Bot.
Uses SQLite with async support via aiosqlite.
"""

import aiosqlite
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from .models import Location, ChatSettings, WeatherStatus, WeatherCheck, AdminUser

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
                    temp_max REAL DEFAULT 35.0,
                    humidity_max REAL DEFAULT 85.0,
                    wind_speed_max REAL DEFAULT 8.0,
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
                    consecutive_not_flyable_checks INTEGER DEFAULT 0,
                    last_notification_type TEXT,
                    last_notification_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (location_id) REFERENCES locations(id),
                    UNIQUE(location_id, date)
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
                    temp_min, temp_max, humidity_max,
                    wind_speed_max, wind_directions, wind_direction_tolerance,
                    dew_point_spread_min, required_conditions_duration_hours,
                    precipitation_probability_max, cloud_cover_max,
                    is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                location.chat_id, location.name, location.latitude, location.longitude,
                location.time_window_start, location.time_window_end,
                location.temp_min, location.temp_max, location.humidity_max,
                location.wind_speed_max, location.wind_directions, location.wind_direction_tolerance,
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
                    temp_min = ?, temp_max = ?, humidity_max = ?,
                    wind_speed_max = ?, wind_directions = ?, wind_direction_tolerance = ?,
                    dew_point_spread_min = ?, required_conditions_duration_hours = ?,
                    precipitation_probability_max = ?, cloud_cover_max = ?,
                    is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                location.name, location.latitude, location.longitude,
                location.time_window_start, location.time_window_end,
                location.temp_min, location.temp_max, location.humidity_max,
                location.wind_speed_max, location.wind_directions, location.wind_direction_tolerance,
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
                # Also delete related weather status and checks
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
        return Location(
            id=row["id"],
            chat_id=row["chat_id"],
            name=row["name"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            time_window_start=row["time_window_start"],
            time_window_end=row["time_window_end"],
            temp_min=row["temp_min"],
            temp_max=row["temp_max"],
            humidity_max=row["humidity_max"],
            wind_speed_max=row["wind_speed_max"],
            wind_directions=row["wind_directions"],
            wind_direction_tolerance=row["wind_direction_tolerance"],
            dew_point_spread_min=row["dew_point_spread_min"],
            required_conditions_duration_hours=row["required_conditions_duration_hours"],
            precipitation_probability_max=row["precipitation_probability_max"],
            cloud_cover_max=row["cloud_cover_max"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
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
                        consecutive_not_flyable_checks = ?,
                        last_notification_type = ?,
                        last_notification_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE location_id = ? AND date = ?
                """, (
                    1 if status.is_flyable else 0,
                    status.flyable_window_start,
                    status.flyable_window_end,
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
                        consecutive_not_flyable_checks,
                        last_notification_type, last_notification_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    status.location_id, status.date,
                    1 if status.is_flyable else 0,
                    status.flyable_window_start, status.flyable_window_end,
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
        return WeatherStatus(
            id=row["id"],
            location_id=row["location_id"],
            date=row["date"],
            is_flyable=bool(row["is_flyable"]),
            flyable_window_start=row["flyable_window_start"],
            flyable_window_end=row["flyable_window_end"],
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
            await self._connection.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old weather checks")
            return deleted
