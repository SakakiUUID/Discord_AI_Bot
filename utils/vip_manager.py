import os
import json
import logging
import datetime
import discord
from discord import app_commands

logger = logging.getLogger("bot.vip_manager")

VIP_CONFIG_FILE = os.path.join("data", "vip_config.json")
VIP_USAGE_FILE = os.path.join("data", "vip_usage.json")

class VIPManager:
    """Manages VIP Whitelist, Bot Super-Admin User IDs, non-VIP daily limits, and persistent storage."""
    def __init__(self):
        self.config_path = VIP_CONFIG_FILE
        self.usage_path = VIP_USAGE_FILE
        self.config = {
            "enabled": True,
            "daily_limit_non_vip": 10,
            "admin_user_ids": [],
            "vip_user_ids": []
        }
        self.usage_data = {}  # { "user_id_str": { "date": "2026-08-13", "count": 3 } }
        self._load_config()
        self._load_usage()

    def _load_config(self):
        os.makedirs("data", exist_ok=True)
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
                logger.info(f"Loaded VIP Config: {len(self.config.get('admin_user_ids', []))} Bot Admins, {len(self.config.get('vip_user_ids', []))} VIPs, Limit: {self.config.get('daily_limit_non_vip')}")
            except Exception as e:
                logger.error(f"Error loading VIP config: {e}")
        else:
            self._save_config()

    def _save_config(self):
        os.makedirs("data", exist_ok=True)
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            logger.info("Saved VIP Config.")
        except Exception as e:
            logger.error(f"Error saving VIP config: {e}")

    def _load_usage(self):
        if os.path.exists(self.usage_path):
            try:
                with open(self.usage_path, "r", encoding="utf-8") as f:
                    self.usage_data = json.load(f)
            except Exception as e:
                logger.error(f"Error loading VIP usage data: {e}")

    def _save_usage(self):
        os.makedirs("data", exist_ok=True)
        try:
            with open(self.usage_path, "w", encoding="utf-8") as f:
                json.dump(self.usage_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving VIP usage data: {e}")

    def is_bot_admin(self, user_id: int) -> bool:
        """Check if user ID is in Bot Admin Whitelist."""
        admins = self.config.get("admin_user_ids", [])
        return user_id in admins

    def is_vip(self, user_id: int) -> bool:
        """Check if user ID is VIP or Bot Admin."""
        if self.is_bot_admin(user_id):
            return True
        return user_id in self.config.get("vip_user_ids", [])

    def _get_today_usage_count(self, user_id: int, today_str: str) -> int:
        """Return today's recorded non-VIP request count for a user."""
        user_record = self.usage_data.get(str(user_id), {})
        if user_record.get("date") != today_str:
            return 0
        return int(user_record.get("count", 0))

    def has_remaining_quota(self, user_id: int) -> bool:
        """Read-only check whether user can send another request today without mutating usage."""
        if not self.config.get("enabled", True) or self.is_vip(user_id):
            return True
        today_str = datetime.date.today().isoformat()
        limit = self.config.get("daily_limit_non_vip", 10)
        return self._get_today_usage_count(user_id, today_str) < limit

    def check_and_increment_usage(self, user_id: int) -> tuple[bool, int, int]:
        """
        Check if user is allowed to generate response and increment count if non-VIP.
        Returns: (is_allowed: bool, current_usage: int, max_limit: int)
        """
        if not self.config.get("enabled", True) or self.is_vip(user_id):
            return True, 0, 0  # Unlimited for VIP or Bot Admins

        today_str = datetime.date.today().isoformat()
        user_key = str(user_id)
        limit = self.config.get("daily_limit_non_vip", 10)
        current_count = self._get_today_usage_count(user_id, today_str)

        if current_count >= limit:
            return False, current_count, limit

        self.usage_data[user_key] = {"date": today_str, "count": current_count + 1}
        self._save_usage()

        return True, current_count + 1, limit

    def add_bot_admin(self, user_id: int) -> bool:
        admins = self.config.setdefault("admin_user_ids", [])
        if user_id not in admins:
            admins.append(user_id)
            self._save_config()
            return True
        return False

    def remove_bot_admin(self, user_id: int) -> bool:
        admins = self.config.setdefault("admin_user_ids", [])
        if user_id in admins:
            admins.remove(user_id)
            self._save_config()
            return True
        return False

    def add_vip(self, user_id: int) -> bool:
        vips = self.config.setdefault("vip_user_ids", [])
        if user_id not in vips:
            vips.append(user_id)
            self._save_config()
            return True
        return False

    def remove_vip(self, user_id: int) -> bool:
        vips = self.config.setdefault("vip_user_ids", [])
        if user_id in vips:
            vips.remove(user_id)
            self._save_config()
            return True
        return False

    def set_daily_limit(self, limit: int):
        self.config["daily_limit_non_vip"] = max(1, limit)
        self._save_config()

# Global VIPManager Singleton Instance
vip_manager = VIPManager()

def require_bot_admin():
    """Custom app command check requiring user ID to be in Bot Admin whitelist."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if vip_manager.is_bot_admin(interaction.user.id):
            return True
        raise app_commands.AppCommandError("NOT_BOT_ADMIN")
    return app_commands.check(predicate)
