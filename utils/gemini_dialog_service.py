import asyncio
import logging
from typing import Optional, List
from google import genai
from google.genai import types

import config
from core.dialog_service import DialogService
from core.memory_service import MemoryService
from core.memory.models import normalize_persona_id, normalize_scope_key, normalize_user_id

logger = logging.getLogger("bot.dialog_service")


class GeminiDialogService(DialogService):
    """Backwards-compatible wrapper for DialogService with injected MemoryService and parallel retrieval."""

    def __init__(
        self,
        api_key: Optional[str] = config.GEMINI_API_KEY,
        memory_service: Optional[MemoryService] = None,
    ):
        client = genai.Client(api_key=api_key) if api_key else None
        super().__init__(ai_client=client)
        self.memory_service = memory_service

    async def generate_response(
        self,
        channel_id: int,
        user_text: str,
        persona_manager,
        context_builder,
        user_id: int = 0,
        channel=None,
        current_message_id: int = 0,
        image_bytes: Optional[bytes] = None,
        image_mime: Optional[str] = None,
        extra_image_parts: Optional[List[types.Part]] = None,
        bot_user=None,
        persona_id: Optional[str] = None,
        query_text: Optional[str] = None,
        skip_vip_check: bool = False,
        is_peer_bot: bool = False,
    ) -> str:
        """
        Execute Discord context fetch and vector memory retrieval in parallel via asyncio.gather,
        then generate response via DialogService.
        """
        guild_id = channel.guild.id if hasattr(channel, "guild") and channel.guild else None
        scope_key = normalize_scope_key(guild_id, user_id, platform="discord")
        norm_uid = normalize_user_id(user_id, platform="discord")
        active_pid = normalize_persona_id(
            persona_id or (persona_manager.current_persona_name if persona_manager else "default")
        )
        effective_query = query_text if query_text is not None else user_text

        async def _fetch_ambient():
            if channel and context_builder and bot_user:
                fetch_images = context_builder.has_image_intent(user_text)
                persona_name = persona_manager.persona_display_name if persona_manager else "AI"
                return await context_builder.fetch_recent_channel_context(
                    channel=channel,
                    current_message_id=current_message_id,
                    fetch_images=fetch_images,
                    persona_display_name=persona_name,
                    bot_user=bot_user
                )
            return "", []

        async def _fetch_memory():
            if self.memory_service:
                return await self.memory_service.retrieve_context(
                    persona_id=active_pid,
                    scope_key=scope_key,
                    user_id=norm_uid,
                    query_text=effective_query,
                )
            return ""

        (ambient_context, ambient_images), memory_context = await asyncio.gather(
            _fetch_ambient(),
            _fetch_memory(),
        )

        return await super().generate_response(
            channel_id=str(channel_id),
            user_text=user_text,
            persona=persona_manager.persona,
            memory_context=memory_context,
            user_vip_id=user_id,
            ambient_context=ambient_context,
            image_bytes=image_bytes,
            image_mime=image_mime,
            extra_image_parts=extra_image_parts,
            ambient_image_parts=ambient_images,
            skip_vip_check=skip_vip_check,
            is_peer_bot=is_peer_bot,
            error_msg_fn=persona_manager.get_msg
        )
