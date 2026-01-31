"""
Main entry point for the Telegram Weather Bot.
Initializes all components and starts the bot.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

import toml
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pytz

from .config import Config, DEFAULT_DATABASE_PATH
from .database import Database
from .weather import OpenWeatherClient, VisualCrossingClient
from .notifications import Notifier
from .handlers import CommandHandlers, ConfigHandler

logger = logging.getLogger(__name__)


class WeatherBot:
    """
    Main bot class that coordinates all components.
    """
    
    def __init__(self):
        """Initialize the bot."""
        self.db: Database = None
        self.openweather: OpenWeatherClient = None
        self.visualcrossing: VisualCrossingClient = None
        self.notifier: Notifier = None
        self.scheduler: AsyncIOScheduler = None
        self.application: Application = None
        self._running = False
    
    async def initialize(self) -> None:
        """
        Initialize all bot components.
        Bot config (API keys, timezone, etc.) is loaded from TOML in DB; if empty, seeded from .env.
        """
        logger.debug("Initializing Weather Bot...")
        
        # Ensure data directory exists (use env/default path before DB config is loaded)
        db_path = os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database and load bot config from TOML in DB
        self.db = Database(db_path)
        await self.db.connect()
        
        bot_config_toml = await self.db.get_bot_config()
        if not bot_config_toml or not bot_config_toml.strip():
            # First run: seed from .env and save to DB
            default_config = {
                "openweather_api_key": os.getenv("OPENWEATHER_API_KEY", ""),
                "visualcrossing_api_key": os.getenv("VISUALCROSSING_API_KEY", ""),
                "timezone": os.getenv("TIMEZONE", "UTC"),
                "polling_interval_minutes": int(os.getenv("POLLING_INTERVAL_MINUTES", "30") or "30"),
                "api_request_delay_seconds": float(os.getenv("API_REQUEST_DELAY_SECONDS", "2") or "2"),
                "log_level": os.getenv("LOG_LEVEL", "INFO") or "INFO",
                "database_path": os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH) or DEFAULT_DATABASE_PATH,
                "admin_user_ids": [
                    int(uid.strip()) for uid in (os.getenv("ADMIN_USER_IDS", "") or "").split(",")
                    if uid.strip().isdigit()
                ],
                "debug_mode": (os.getenv("DEBUG_MODE", "false") or "false").lower() in ("true", "1", "yes"),
            }
            await self.db.set_bot_config(toml.dumps(default_config))
            Config.set_runtime_config(default_config)
        else:
            parsed = toml.loads(bot_config_toml)
            Config.set_runtime_config(parsed)
        
        # Now setup logging and validate (Config is from TOML)
        Config.setup_logging()
        errors = Config.validate()
        if errors:
            for error in errors:
                logger.error(f"Config error: {error}")
            raise ValueError("Invalid configuration. Check bot config (TOML in DB) or .env for first run.")
        
        Config.ensure_data_dir()
        timezone = Config.get_timezone()
        
        # Initialize weather API clients
        self.openweather = OpenWeatherClient(Config.OPENWEATHER_API_KEY)
        self.visualcrossing = VisualCrossingClient(Config.VISUALCROSSING_API_KEY)
        
        # Build telegram application
        self.application = (
            Application.builder()
            .token(Config.BOT_TOKEN)
            .build()
        )
        
        # Initialize notifier
        self.notifier = Notifier(
            bot=self.application.bot,
            db=self.db,
            openweather=self.openweather,
            visualcrossing=self.visualcrossing,
            timezone=timezone
        )
        
        # Setup handlers
        self._setup_handlers()
        
        # Setup scheduler
        self._setup_scheduler(timezone)
        
        logger.debug("Weather Bot initialized successfully")
    
    def _setup_handlers(self) -> None:
        """Setup Telegram command handlers."""
        cmd_handlers = CommandHandlers(self.db, self.notifier)
        config_handler = ConfigHandler(self.db)
        
        # Conversation handlers first so /set_config_locations and /set_config_bot are always caught
        self.application.add_handler(config_handler.get_conversation_handler())
        self.application.add_handler(config_handler.get_conversation_handler_bot())
        
        # Add command handlers
        self.application.add_handler(
            CommandHandler("start", cmd_handlers.start_command)
        )
        self.application.add_handler(
            CommandHandler("help", cmd_handlers.help_command)
        )
        self.application.add_handler(
            CommandHandler("list_locations", cmd_handlers.list_locations_command)
        )
        self.application.add_handler(
            CommandHandler("status", cmd_handlers.status_command)
        )
        self.application.add_handler(
            CommandHandler("check", cmd_handlers.check_command)
        )
        self.application.add_handler(
            CommandHandler("weather", cmd_handlers.weather_command)
        )
        self.application.add_handler(
            CommandHandler("get_config_locations", cmd_handlers.get_config_command)
        )
        self.application.add_handler(
            CommandHandler("flywindow", cmd_handlers.flywindow_command)
        )
        # /get_config_bot — show current bot TOML (admin only)
        self.application.add_handler(
            CommandHandler("get_config_bot", cmd_handlers.get_config_bot_command)
        )

        # Handle unknown commands
        self.application.add_handler(
            MessageHandler(filters.COMMAND, cmd_handlers.unknown_command)
        )
        
        logger.debug("Command handlers registered")
    
    def _setup_scheduler(self, timezone: pytz.timezone) -> None:
        """Setup periodic weather check scheduler."""
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        
        # Add weather check job
        self.scheduler.add_job(
            self._scheduled_weather_check,
            trigger=IntervalTrigger(minutes=Config.POLLING_INTERVAL_MINUTES),
            id="weather_check",
            name="Periodic weather check",
            replace_existing=True,
            next_run_time=datetime.now(timezone)  # Run immediately on start
        )
        
        # Add cleanup job (daily at 3 AM)
        self.scheduler.add_job(
            self._scheduled_cleanup,
            trigger="cron",
            hour=3,
            minute=0,
            id="cleanup",
            name="Database cleanup",
            replace_existing=True
        )
        
        logger.debug(
            f"Scheduler configured: weather check every {Config.POLLING_INTERVAL_MINUTES} minutes"
        )
    
    async def _scheduled_weather_check(self) -> None:
        """Scheduled job to check weather for all locations."""
        logger.debug("Running scheduled weather check")
        try:
            await self.notifier.check_all_locations()
            logger.debug("Scheduled weather check completed")
        except Exception as e:
            logger.error(f"Error in scheduled weather check: {e}")
    
    async def _scheduled_cleanup(self) -> None:
        """Scheduled job to clean up old records."""
        logger.debug("Running scheduled cleanup")
        try:
            deleted = await self.db.cleanup_old_checks(days_to_keep=7)
            logger.debug(f"Cleanup completed: {deleted} old records deleted")
        except Exception as e:
            logger.error(f"Error in scheduled cleanup: {e}")
    
    async def start(self) -> None:
        """Start the bot."""
        if self._running:
            logger.warning("Bot is already running")
            return
        
        self._running = True
        logger.debug("Starting Weather Bot...")
        
        # Start scheduler
        self.scheduler.start()
        
        # Start bot polling
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES
        )
        
        logger.debug("Weather Bot is running")
        
        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        """Stop the bot gracefully."""
        logger.debug("Stopping Weather Bot...")
        self._running = False
        
        # Stop scheduler
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        
        # Stop bot (updater may already be stopped)
        if self.application:
            try:
                if self.application.updater:
                    await self.application.updater.stop()
            except RuntimeError:
                pass
            await self.application.stop()
            await self.application.shutdown()
        
        # Close API clients
        if self.openweather:
            await self.openweather.close()
        if self.visualcrossing:
            await self.visualcrossing.close()
        
        # Close database
        if self.db:
            await self.db.close()
        
        logger.debug("Weather Bot stopped")


async def main() -> None:
    """Main entry point."""
    bot = WeatherBot()
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.debug("Received shutdown signal")
        asyncio.create_task(bot.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await bot.initialize()
        await bot.start()
    except KeyboardInterrupt:
        logger.debug("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
    finally:
        await bot.stop()


def run() -> None:
    """Run the bot (blocking)."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
