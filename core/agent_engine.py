import os
import logging
from typing import Optional, List
from google import genai
from google.genai import types

import config
from core.models.incoming import IncomingMessage
from core.models.outgoing import OutgoingResponse
from core.persona_service import PersonaService
from core.memory_service import MemoryService
from core.memory.models import normalize_persona_id, normalize_scope_key, normalize_user_id
from core.dialog_service import DialogService
from core.context_manager import ContextManager

logger = logging.getLogger("core.agent_engine")


class AgentEngine:
    """Unified AI Agent Orchestrator coordinating Persona, Memory, Context, and LLM Dialog Generation."""

    def __init__(
        self,
        api_key: Optional[str] = config.GEMINI_API_KEY,
        personas_dir: str = "data/personas",
        data_dir: str = "data",
        db_path: Optional[str] = None,
        memory_service: Optional[MemoryService] = None,
    ):
        if api_key:
            self.ai_client = genai.Client(api_key=api_key)
            logger.info("Gemini AI Client initialized in AgentEngine.")
        else:
            self.ai_client = None
            logger.warning("GEMINI_API_KEY missing. AgentEngine running in mock/fallback mode.")

        self.persona_service = PersonaService(personas_dir=personas_dir)
        self.memory_service = memory_service or MemoryService(
            ai_client=self.ai_client,
            data_dir=data_dir,
            db_path=db_path or os.path.join(data_dir, "memory.db"),
        )
        self.dialog_service = DialogService(ai_client=self.ai_client)
        self.context_manager = ContextManager()

    async def initialize(self):
        """Initialize backing stores and background memory worker."""
        await self.memory_service.initialize()

    async def close(self):
        """Shutdown background workers and close backing stores."""
        await self.memory_service.close()

    def check_wake_word(self, text: str) -> bool:
        """Check if message contains active persona's name or wake-word aliases."""
        if not text:
            return False
        clean_text = text.lower()
        aliases = self.persona_service.get_active_persona_aliases()
        return any(alias.lower() in clean_text for alias in aliases if alias)

    async def process_message(
        self,
        msg: IncomingMessage,
        ambient_context: str = "",
        extra_image_parts: Optional[List[types.Part]] = None,
        user_vip_id: int = 0
    ) -> Optional[OutgoingResponse]:
        """
        Main entry point to process an IncomingMessage:
        1. Buffer message into SQLite pending_messages for background memory extraction.
        2. Check if direct chat or wake word hit.
        3. If triggered, retrieve persona-isolated hybrid vector memory and generate response.
        """
        if not self.memory_service.is_initialized:
            await self.memory_service.initialize()

        persona_id = normalize_persona_id(self.persona_service.current_persona_name)
        effective_guild = msg.guild_id if msg.guild_id is not None else (
            None if msg.is_direct_chat and msg.channel_type == "discord" else msg.channel_id
        )
        scope_key = normalize_scope_key(effective_guild, msg.user_id, platform=msg.channel_type)
        user_id_namespaced = normalize_user_id(msg.user_id, platform=msg.channel_type)

        # 1. Background memory buffering
        await self.memory_service.add_message(
            channel_id=msg.channel_id,
            scope_key=scope_key,
            persona_id=persona_id,
            user_id=user_id_namespaced,
            username=msg.username,
            content=msg.text,
            image_bytes=msg.image_bytes,
            image_mime=msg.image_mime,
            platform=msg.channel_type,
        )

        # 2. Check dialogue trigger condition
        is_wake_hit = self.check_wake_word(msg.text)
        should_reply = msg.is_direct_chat or is_wake_hit

        if not should_reply:
            return None

        # 3. Retrieve persona-isolated dual-layer + vector memory context
        memory_context = await self.memory_service.retrieve_context(
            persona_id=persona_id,
            scope_key=scope_key,
            user_id=user_id_namespaced,
            query_text=msg.text,
        )

        # 4. Format prompt
        reply_prefix = f"【你正回覆的目標訊息】: {msg.reply_target_text}\n" if msg.reply_target_text else ""
        full_user_text = f"{reply_prefix}[{msg.username}]: {msg.text or '（這張圖片怎麼樣？）'}"

        reply_text = await self.dialog_service.generate_response(
            channel_id=msg.channel_id,
            user_text=full_user_text,
            persona=self.persona_service.persona,
            memory_context=memory_context,
            user_vip_id=user_vip_id,
            ambient_context=ambient_context,
            image_bytes=msg.image_bytes,
            image_mime=msg.image_mime,
            extra_image_parts=extra_image_parts,
            error_msg_fn=self.persona_service.get_msg
        )

        return OutgoingResponse(reply_text=reply_text)


# Global singleton instance for shared use (lazy-initialized, no import-time tasks)
agent_engine = AgentEngine()
