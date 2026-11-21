"""
title: Pin Message
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://github.com/open-webui
version: 0.1.1
license: MIT
required_open_webui_version: 0.8.12
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMSA4LjI1YzAtMi40ODUtMi4wOTktNC41LTQuNjg4LTQuNS0xLjkzNSAwLTMuNTk3IDEuMTI2LTQuMzEyIDIuNzMzLS43MTUtMS42MDctMi4zNzctMi43MzMtNC4zMTMtMi43MzNDNS4xIDMuNzUgMyA1Ljc2NSAzIDguMjVjMCA3LjIyIDkgMTIgOSAxMnM5LTQuNzggOS0xMloiLz48L3N2Zz4=
"""

from pydantic import BaseModel, Field
from typing import Optional


class Action:
    """
    Action function that toggles the 'favorite' (pin) status on a message.

    Pinned messages appear with a heart icon in the Chat Overview, making them
    easy to find and navigate to later.
    """

    class Valves(BaseModel):
        """Admin-level configuration options."""

        priority: int = Field(
            default=0,
            description="Priority level for button sorting in the UI (lower numbers appear first).",
        )

        show_notification: bool = Field(
            default=True,
            description="Show notification when pinning/unpinning a message",
        )

    class UserValves(BaseModel):
        """User-level configuration options."""

        show_notification: bool = Field(
            default=True,
            description="Show notification when pinning/unpinning a message",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__=None,
        __event_call__=None,
        __request__=None,
    ) -> Optional[dict]:
        """
        Toggle the favorite/pin status on the current message.
        """

        # Get user valves
        user_valves = __user__.get("valves", None) if __user__ else None
        if user_valves and isinstance(user_valves, self.UserValves):
            uv = user_valves
        elif user_valves and isinstance(user_valves, dict):
            uv = self.UserValves(**user_valves)
        else:
            uv = self.UserValves()

        # Determine if we should show notifications
        show_notif = uv.show_notification and self.valves.show_notification

        # Get chat_id and message_id from body
        chat_id = body.get("chat_id")
        message_id = body.get("id")  # The message that triggered the action

        if not chat_id or not message_id:
            if show_notif:
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {
                            "type": "error",
                            "content": "Missing chat or message ID",
                        },
                    }
                )
            return None

        print(f"[Pin Message] Toggling pin for message {message_id} in chat {chat_id}")

        try:
            from open_webui.models.chats import Chats

            # Get the current chat (AWAITED for async migration)
            chat = await Chats.get_chat_by_id(chat_id)

            if not chat:
                if show_notif:
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {"type": "error", "content": "Chat not found"},
                        }
                    )
                return None

            # The chat.chat field contains the chat data including history and messages
            chat_data = chat.chat if isinstance(chat.chat, dict) else {}

            print(
                f"[Pin Message] Chat data keys: {chat_data.keys() if chat_data else 'None'}"
            )

            history = chat_data.get("history", {})
            messages = history.get("messages", {})

            print(f"[Pin Message] Found {len(messages)} messages in history")

            # Find the message
            if message_id not in messages:
                print(
                    f"[Pin Message] Message {message_id} not found. Available: {list(messages.keys())[:5]}..."
                )
                if show_notif:
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {
                                "type": "error",
                                "content": "Message not found in chat",
                            },
                        }
                    )
                return None

            message = messages[message_id]

            # Toggle favorite status
            current_favorite = message.get("favorite", False)
            new_favorite = not current_favorite

            print(
                f"[Pin Message] Current favorite: {current_favorite} -> New favorite: {new_favorite}"
            )

            message["favorite"] = new_favorite

            # Make sure the change is reflected in the chat_data
            chat_data["history"]["messages"][message_id]["favorite"] = new_favorite

            print("[Pin Message] Saving chat with updated favorite status...")

            # Save the updated chat (AWAITED for async migration)
            updated_chat = await Chats.update_chat_by_id(id=chat_id, chat=chat_data)

            print(f"[Pin Message] Update result: {updated_chat is not None}")

            if updated_chat:
                action_text = "Pinned" if new_favorite else "Unpinned"
                print(f"[Pin Message] {action_text} message {message_id}")

                # Emit chat:message:favorite event to update frontend cache
                # This must happen BEFORE the frontend saves, so the new value persists
                await __event_emitter__(
                    {
                        "type": "chat:message:favorite",
                        "data": {"favorite": new_favorite},
                    }
                )

                if show_notif:
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {
                                "type": "success",
                                "content": f"{'📌 ' if new_favorite else ''}Message {action_text.lower()}",
                            },
                        }
                    )
            else:
                if show_notif:
                    await __event_emitter__(
                        {
                            "type": "notification",
                            "data": {
                                "type": "error",
                                "content": "Failed to update message",
                            },
                        }
                    )

            return None

        except Exception as e:
            print(f"[Pin Message] Error: {e}")
            import traceback

            traceback.print_exc()

            if show_notif:
                await __event_emitter__(
                    {
                        "type": "notification",
                        "data": {"type": "error", "content": f"Error: {str(e)}"},
                    }
                )
            return None
