"""
title: Regenerate Follow-ups
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://github.com/open-webui
version: 0.1.1
license: MIT
required_open_webui_version: 0.8.12
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiI+PHBhdGggZD0iTTIxIDE1YTIgMiAwIDAgMS0yIDJIN2wtNCA0VjVhMiAyIDAgMCAxIDItMmgxNGEyIDIgMCAwIDEgMiAyeiIvPjxwYXRoIGQ9Ik04IDEwaC4wMU0xMiAxMGguMDFNMTYgMTBoLjAxIi8+PC9zdmc+
"""

from pydantic import BaseModel, Field
from typing import Optional
import json

from open_webui.routers.tasks import generate_follow_ups
from open_webui.socket.main import get_event_emitter


class Action:
    """
    Action that regenerates the assistant's follow-up suggestions.
    This action calls the follow-up generation task directly and emits
    the new follow-ups to the UI, without regenerating the main response.
    """

    class Valves(BaseModel):
        """Admin-level configuration options (system-wide defaults)."""

        priority: int = Field(
            default=0,
            description="Priority level for button sorting in the UI (lower numbers appear first).",
        )
        ENABLE_NOTIFICATION: bool = Field(
            default=True,
            description="Enable success notification toast after regeneration",
        )
        ENABLE_EVENT_EMITTERS: bool = Field(
            default=False,
            description="Enable status event updates during regeneration",
        )
        ENABLE_DEBUG_LOGS: bool = Field(
            default=True,
            description="Enable debug logging to console",
        )

    class UserValves(BaseModel):
        """User-level configuration options (per-user overrides)."""

        ENABLE_NOTIFICATION: bool = Field(
            default=True,
            description="Enable success notification toast after regeneration",
        )
        ENABLE_EVENT_EMITTERS: bool = Field(
            default=False,
            description="Enable status event updates during regeneration",
        )
        ENABLE_DEBUG_LOGS: bool = Field(
            default=True,
            description="Enable debug logging to console",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _get_user_valves(self, __user__: Optional[dict]) -> "Action.UserValves":
        """Get user valves with proper Pydantic instantiation."""
        if not __user__:
            return self.UserValves()
        user_valves_data = __user__.get("valves", {})
        if isinstance(user_valves_data, dict):
            return self.UserValves(**user_valves_data)
        elif user_valves_data:
            return user_valves_data
        return self.UserValves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
        __event_call__=None,
        __request__=None,
    ) -> Optional[dict]:
        """
        Regenerate follow-up suggestions for the assistant's response.
        """
        user_valves = self._get_user_valves(__user__)

        # Logic: Debug is OR (either can enable), Notif/Emitters are AND (both must allow)
        enable_debug = self.valves.ENABLE_DEBUG_LOGS or user_valves.ENABLE_DEBUG_LOGS
        enable_notification = (
            self.valves.ENABLE_NOTIFICATION and user_valves.ENABLE_NOTIFICATION
        )
        enable_event_emitters = (
            self.valves.ENABLE_EVENT_EMITTERS and user_valves.ENABLE_EVENT_EMITTERS
        )

        # --- Helper functions to strictly respect valve toggles ---
        async def emit_status(description: str, done: bool = False):
            if __event_emitter__ and enable_event_emitters:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": description, "done": done},
                    }
                )

        async def emit_notif(content: str, notif_type: str = "success"):
            if __event_emitter__ and enable_notification:
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {"type": notif_type, "content": content},
                    }
                )

        async def emit_msg(content: str):
            if __event_emitter__ and enable_event_emitters:
                await __event_emitter__(
                    {"type": "message", "data": {"content": content}}
                )

        # ----------------------------------------------------------

        await emit_status("Regenerating follow-ups...")

        try:
            chat_id = body.get("chat_id", "")
            messages = body.get("messages", [])

            # Find last assistant message
            assistant_message = next(
                (msg for msg in reversed(messages) if msg.get("role") == "assistant"),
                None,
            )

            if not assistant_message:
                await emit_msg(
                    "**Regenerate Follow-ups**\n\nNo assistant message found."
                )
                await emit_status("Done", True)
                return None

            message_id = assistant_message.get("id")
            model_id = assistant_message.get("model") or body.get("model", "")

            if not message_id or not model_id:
                await emit_msg(
                    "**Regenerate Follow-ups**\n\nUnable to regenerate: message ID or model not found."
                )
                await emit_status("Done", True)
                return None

            # Prepare User object for internal task call
            class UserLike:
                def __init__(self, data):
                    self.id = data.get("id", "")
                    self.email = data.get("email", "")
                    self.name = data.get("name", "")
                    self.role = data.get("role", "")
                    self.model_dump = lambda: data

            user_obj = UserLike(__user__ or {})

            # Call internal generation task (Awaited for 0.9.0)
            response = await generate_follow_ups(
                __request__,
                {
                    "model": model_id,
                    "messages": messages,
                    "message_id": message_id,
                    "chat_id": chat_id,
                },
                user_obj,
            )

            # Check for disabled status in response
            from starlette.responses import JSONResponse

            if isinstance(response, JSONResponse):
                content = json.loads(response.body)
                if content.get("detail") == "Follow-up generation is disabled":
                    await emit_notif(
                        "Follow-up generation is disabled in system settings.", "error"
                    )
                    await emit_status("Done", True)
                    return None

            # Parse and Emit results
            if isinstance(response, dict):
                content = (
                    response.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                # Extract JSON list from text content
                start, end = content.find("{"), content.rfind("}") + 1
                follow_ups_data = (
                    json.loads(content[start:end]).get("follow_ups", [])
                    if (start >= 0 and end > start)
                    else []
                )

                # Target the specific message ID for the UI update
                emitter = await get_event_emitter(
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "session_id": "",
                        "user_id": __user__.get("id") if __user__ else "",
                    }
                )

                await emitter(
                    {
                        "type": "chat:message:follow_ups",
                        "data": {"follow_ups": follow_ups_data},
                    }
                )

            await emit_notif("Follow-ups regenerated successfully")
            await emit_status("Done", True)

        except Exception as e:
            if enable_debug:
                print(f"Regenerate follow-ups error: {e}")
            await emit_msg(f"**Regenerate Follow-ups**\n\nError: {str(e)}")
            await emit_status("Done", True)

        return None
