import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from google import genai
from google.genai import types

import config

logger = logging.getLogger("core.proactive_judge")

VALID_DECISIONS = {"reply", "react", "pass"}
_DISCORD_CUSTOM_EMOJI_RE = re.compile(r"<a?:[^:>]+:\d+>")


@dataclass
class ProactiveDecision:
    """Structured decision emitted by the lightweight proactive reply judge."""

    decision: str  # "reply", "react", or "pass"
    emoji: Optional[str] = None
    reason: str = ""


def is_valid_unicode_emoji(emoji: Optional[str]) -> bool:
    """Return True if `emoji` looks like a single standard Unicode emoji (not Discord custom tag or text)."""
    if not isinstance(emoji, str):
        return False
    candidate = emoji.strip()
    if not candidate or len(candidate) > 16:
        return False
    if _DISCORD_CUSTOM_EMOJI_RE.search(candidate):
        return False
    if any(ch.isspace() or ch.isascii() and (ch.isalnum() or ch in "[]{}()<>:;\"',.?!/\\") for ch in candidate):
        return False
    return any(ord(ch) >= 0x2000 for ch in candidate)


def parse_decision_json(raw_text: str) -> ProactiveDecision:
    """Parse structured JSON decision safely, degrading to `pass` on malformed output."""
    text = (raw_text or "").strip()
    if not text:
        return ProactiveDecision(decision="pass", reason="empty response")

    # Strip accidental markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()

    try:
        payload = json.loads(text)
    except Exception:
        return ProactiveDecision(decision="pass", reason="invalid json")

    if not isinstance(payload, dict):
        return ProactiveDecision(decision="pass", reason="non-dict json")

    decision = str(payload.get("decision", "pass")).strip().lower()
    reason = str(payload.get("reason", "")).strip()
    if decision not in VALID_DECISIONS:
        return ProactiveDecision(decision="pass", reason=reason or "unknown decision")

    if decision == "react":
        emoji = (payload.get("emoji") or "").strip()
        if not is_valid_unicode_emoji(emoji):
            return ProactiveDecision(decision="pass", reason="invalid react emoji")
        return ProactiveDecision(decision="react", emoji=emoji, reason=reason)

    return ProactiveDecision(decision=decision, reason=reason)


class ProactiveJudge:
    """Lightweight Gemini JSON classifier deciding whether to reply, react, or pass during a hot window."""

    def __init__(self, ai_client: Optional[genai.Client] = None):
        self.ai_client = ai_client

    async def judge(
        self,
        persona_name: str,
        persona_summary: str,
        ambient_context: str,
        speaker_name: str,
        latest_message: str,
        is_active_participant: bool,
        active_participant_names: Optional[List[str]] = None,
        image_bytes: Optional[bytes] = None,
        image_mime: Optional[str] = None,
    ) -> ProactiveDecision:
        """Evaluate whether the bot should reply, add a Unicode reaction, or stay silent (`pass`)."""
        if not self.ai_client:
            return ProactiveDecision(decision="pass", reason="ai_client missing")

        participants_str = "、".join(active_participant_names or []) or "無"
        speaker_role = "既有話題參與者（剛剛就在跟大家／你互動）" if is_active_participant else "同頻道尚未加入此話題的群友"
        snippet = (persona_summary or "").strip()[:500]

        system_instruction = (
            f"你是群聊中的「{persona_name}」對話意圖裁判。\n"
            f"角色簡介：{snippet}\n\n"
            "現在頻道正處於你剛參與的『熱聊延續視窗』，請判斷是否要對剛發出的這則免 `@` 訊息接話。\n"
            "只能輸出嚴格 JSON：{\"decision\": \"reply\" | \"react\" | \"pass\", \"emoji\": \"單一Unicode表情或空字串\", \"reason\": \"簡短理由\"}\n\n"
            "判斷準則：\n"
            "1. reply：發話者明顯在接續跟你剛才的對話、向你提問或拋梗，或新群友自然插進你正在參與的話題想互動。\n"
            "2. react：適合用一個標準 Unicode Emoji 表達共鳴/按讚/笑而不需插嘴打斷（emoji 必填單一標準 Unicode 表情如 👍、😂、❤️、👀、✨，嚴禁 Discord 自訂表情 <:...:...> 或文字）。\n"
            "3. pass：他們明顯轉去跟其他群友聊無關的另一件事、或只是簡短結束語不需要再接話、或插話會顯得突兀。不確定時傾向選 pass。"
        )

        prompt_parts = [
            f"【目前活躍參與者名單】：{participants_str}",
            f"【本次發言者】：{speaker_name}（身分：{speaker_role}）",
        ]
        if ambient_context:
            prompt_parts.append(ambient_context.strip())
        prompt_parts.append(f"【剛發出的最新訊息 ({speaker_name})】：\n{latest_message or '（發送了圖片）'}")

        contents_parts: List[types.Part] = []
        if image_bytes:
            contents_parts.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime or "image/png"))
        contents_parts.append(types.Part.from_text(text="\n\n".join(prompt_parts)))

        try:
            response = await asyncio.to_thread(
                self.ai_client.models.generate_content,
                model=config.GEMINI_MODEL,
                contents=[types.Content(role="user", parts=contents_parts)],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=128,
                ),
            )
            decision = parse_decision_json(getattr(response, "text", "") or "")
            logger.info(
                f"🎯 [ProactiveJudge] speaker={speaker_name} active={is_active_participant} "
                f"decision={decision.decision} emoji={decision.emoji or '-'} reason={decision.reason}"
            )
            return decision
        except Exception as e:
            logger.warning(f"⚠️ [ProactiveJudge] Judge call failed, degrading to pass: {e}")
            return ProactiveDecision(decision="pass", reason="judge error")
