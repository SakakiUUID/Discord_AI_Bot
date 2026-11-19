import re
import logging
import asyncio
from typing import Dict, List, Optional, Union
from google import genai
from google.genai import types

import config
from utils.vip_manager import vip_manager

logger = logging.getLogger("core.dialog_service")


class DialogService:
    """Manages Gemini multi-turn conversation histories and LLM prompt generation across channels."""

    def __init__(self, ai_client: Optional[genai.Client] = None):
        self.ai_client = ai_client
        self.channel_histories: Dict[str, List[types.Content]] = {}

    def reset_channel_history(self, channel_id: Union[str, int]) -> bool:
        """Clear conversation history for a specific channel. Returns True if history existed."""
        cid = str(channel_id)
        if cid in self.channel_histories:
            self.channel_histories.pop(cid)
            return True
        return False

    def clear_all_histories(self):
        """Clear all conversation histories across all channels."""
        self.channel_histories.clear()

    @staticmethod
    def clean_speech_text(text: str) -> str:
        """Clean character prefixes (e.g., '[莫寧]: '), residual tags, and surrounding quotes."""
        clean = re.sub(r"\[(?:MEME|EMOTION):\s*[^\]]+\]", "", text, flags=re.IGNORECASE).strip()
        clean = re.sub(r"^\[?[^:\]\n]+\]?:\s*", "", clean).strip()
        clean = clean.strip("\"'「」")
        return clean

    def _get_genai_config(
        self,
        persona: str,
        memory_context: str = "",
        enable_search: bool = True,
        is_peer_bot: bool = False,
    ) -> types.GenerateContentConfig:
        """Assemble System Instruction combining character persona prompt, memory context, peer hint, and Google Search."""
        combined_instruction = persona
        if memory_context:
            combined_instruction += f"\n\n{memory_context}"
        if is_peer_bot:
            combined_instruction += (
                "\n\n【同伴互動情境提示】：\n"
                "現在跟你搭話的是另一位 AI 夥伴，請保持自然、簡短（1~2 句）的日常互動口吻，"
                "像朋友之間接話一樣，不要長篇大論或強行延續話題，適時留白或句點也很自然。"
            )

        tools = []
        if enable_search and config.ENABLE_GOOGLE_SEARCH:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
            combined_instruction += (
                "\n\n【聯網搜尋與時效指示】：\n"
                "你擁有即時聯網檢索最新資訊的能力。當對話涉及最新時事、近期活動日程、最新遊戲更新、即時天氣、查詢今日日期或即時事實時，請務必主動透過 Google 搜尋獲取精確事實。"
                "檢索完畢後，請務必將資訊消化並轉化為你的角色第一人稱口吻來向對話者說明，切勿機械式照抄搜尋結果！"
            )

        return types.GenerateContentConfig(
            system_instruction=combined_instruction,
            tools=tools if tools else None,
            temperature=0.75,
            top_p=0.9,
            max_output_tokens=2048,
        )

    async def generate_response(
        self,
        channel_id: Union[str, int],
        user_text: str,
        persona: str,
        memory_context: str = "",
        user_vip_id: int = 0,
        ambient_context: str = "",
        image_bytes: Optional[bytes] = None,
        image_mime: Optional[str] = None,
        extra_image_parts: Optional[List[types.Part]] = None,
        ambient_image_parts: Optional[List[types.Part]] = None,
        enable_search: bool = True,
        skip_vip_check: bool = False,
        is_peer_bot: bool = False,
        error_msg_fn=None
    ) -> str:
        """
        Generate clean response text via Gemini LLM.
        `ambient_context` and `ambient_image_parts` are injected transiently per-request
        and intentionally excluded from persistent `channel_histories` to prevent token bloat.
        """
        if not self.ai_client:
            return error_msg_fn("api_key_missing") if error_msg_fn else "（系統訊息：請先設定 GEMINI_API_KEY）"

        # Check VIP & Daily Usage Limit (peer bots are exempted)
        if user_vip_id > 0 and not (skip_vip_check or is_peer_bot):
            is_allowed, usage_count, max_limit = vip_manager.check_and_increment_usage(user_vip_id)
            if not is_allowed:
                logger.info(f"User {user_vip_id} exceeded daily usage limit ({usage_count}/{max_limit}). Response blocked.")
                return error_msg_fn("limit_exceeded", limit=max_limit) if error_msg_fn else f"（今日對話額度已用完，每日上限：{max_limit} 次）"

        cid = str(channel_id)
        if cid not in self.channel_histories:
            self.channel_histories[cid] = []

        history = self.channel_histories[cid]

        # Build clean persistent user turn (without ambient context / ambient images)
        clean_parts: List[types.Part] = []
        if extra_image_parts:
            clean_parts.extend(extra_image_parts)
        if image_bytes:
            clean_parts.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime or "image/png"))
        clean_user_text = (user_text or "").strip()
        if clean_user_text:
            clean_parts.append(types.Part.from_text(text=clean_user_text))

        if not clean_parts and not (ambient_context or "").strip():
            return error_msg_fn("empty_input") if error_msg_fn else "（訊息內容為空）"

        if not clean_parts and ambient_context:
            clean_parts.append(types.Part.from_text(text=clean_user_text or "（繼續對話）"))

        user_content = types.Content(role="user", parts=clean_parts)
        history.append(user_content)

        if len(history) > 16:
            history = history[-16:]
            self.channel_histories[cid] = history

        # Assemble transient request contents with ambient context & ambient images only for this call
        if ambient_context or ambient_image_parts:
            transient_parts: List[types.Part] = []
            if ambient_image_parts:
                transient_parts.extend(ambient_image_parts)
            if extra_image_parts:
                transient_parts.extend(extra_image_parts)
            if image_bytes:
                transient_parts.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime or "image/png"))
            transient_text = f"{ambient_context}{clean_user_text}".strip()
            if transient_text:
                transient_parts.append(types.Part.from_text(text=transient_text))
            request_contents = history[:-1] + [types.Content(role="user", parts=transient_parts)]
        else:
            request_contents = list(history)

        try:
            response = await asyncio.to_thread(
                self.ai_client.models.generate_content,
                model=config.GEMINI_MODEL,
                contents=request_contents,
                config=self._get_genai_config(
                    persona=persona,
                    memory_context=memory_context,
                    enable_search=enable_search,
                    is_peer_bot=is_peer_bot,
                )
            )

            # Log search queries if search grounding was triggered
            if response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, "grounding_metadata") and candidate.grounding_metadata:
                    gm = candidate.grounding_metadata
                    queries = getattr(gm, "web_search_queries", None)
                    chunks = getattr(gm, "grounding_chunks", None)
                    if queries:
                        logger.info(f"🌐 [Google Search Grounding] Triggered web search queries: {queries}")
                    elif chunks:
                        logger.info(f"🌐 [Google Search Grounding] Grounded with {len(chunks)} search chunks.")

            raw_text = (response.text or "").strip()
            if not raw_text:
                if history and history[-1] is user_content:
                    history.pop()
                return error_msg_fn("blank_response") if error_msg_fn else "（AI 角色暫時放空了...）"

            # Append model's response to multi-turn conversation history
            model_content = types.Content(role="model", parts=[types.Part.from_text(text=raw_text)])
            history.append(model_content)

            if len(history) > 16:
                history = history[-16:]
                self.channel_histories[cid] = history

            return self.clean_speech_text(raw_text)

        except Exception as e:
            if history and history[-1] is user_content:
                history.pop()
            logger.error(f"Error calling Gemini API in DialogService: {e}")
            err_msg = error_msg_fn("api_error") if error_msg_fn else "（系統遇到了一點問題，請稍後再試）"
            return f"{err_msg}\n`[Detail: {e}]`"
