import logging
from typing import Optional
from google import genai

import config
from core.memory_service import MemoryService, DATA_DIR, GUILD_CULTURES_FILE, USER_PROFILES_FILE

logger = logging.getLogger("bot.memory_manager")


class MemoryManager(MemoryService):
    """
    Backwards-compatible subclass of MemoryService.
    No module-level singleton is instantiated on import; manage lifecycle via Cog/App.
    """

    def __init__(
        self,
        api_key: Optional[str] = config.GEMINI_API_KEY,
        data_dir: str = DATA_DIR,
        db_path: Optional[str] = None,
    ):
        client = genai.Client(api_key=api_key) if api_key else None
        super().__init__(ai_client=client, data_dir=data_dir, db_path=db_path)
