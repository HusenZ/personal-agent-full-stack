import json
from typing import Any

SSE_MEDIA_TYPE = "text/event-stream"

SSE_HEADERS = {
    # A cached or transformed event stream arrives all at once or not at all.
    "Cache-Control": "no-cache, no-transform",
    # Stop proxies like nginx from buffering the stream.
    "X-Accel-Buffering": "no",
}


def encode_event(event: str, data: dict[str, Any]) -> str:
    """One server-sent event, with its payload as a single line of JSON.

    JSON escapes newlines inside strings, so a payload can never contain the
    blank line that would end the event early and let content forge another.
    """
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
