import json

TYPE_CHAT = "chat"
TYPE_SYSTEM = "system"
TYPE_COMMAND = "command"


class ProtocolError(ValueError):
    """Raised when a line can't be parsed as a valid protocol message."""


def encode(message: dict) -> str:
    # ensure_ascii=False keeps non-ASCII text as-is (UTF-8 on the wire) instead
    # of \uXXXX escapes, which would expand e.g. an emoji from 1 char to 12.
    return json.dumps(message, ensure_ascii=False) + "\n"


def decode(line: str) -> dict:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"Invalid JSON: {e}") from e

    if not isinstance(message, dict):
        raise ProtocolError(f"Message must be a JSON object, got {type(message).__name__}")
    if "type" not in message:
        raise ProtocolError("Message missing required 'type' field")
    if "payload" not in message:
        raise ProtocolError("Message missing required 'payload' field")

    return message


def make_chat(sender: str, text: str) -> dict:
    """Server -> client broadcast. The sender is the server-vouched username."""
    return {"type": TYPE_CHAT, "payload": {"sender": sender, "text": text}}


def make_chat_request(text: str) -> dict:
    """Client -> server chat message. No sender: the server stamps the
    authenticated username on the broadcast copy (see make_chat)."""
    return {"type": TYPE_CHAT, "payload": {"text": text}}


def make_system(event: str, **fields) -> dict:
    return {"type": TYPE_SYSTEM, "payload": {"event": event, **fields}}

def make_command(name: str, args: list[str] | None = None) -> dict:
    return {"type": TYPE_COMMAND, "payload": {"name": name, "args": list(args or [])}}
