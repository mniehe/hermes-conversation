"""Builders for the server-sent event bodies Hermes streams."""

ROLE_CHUNK = (
    '{"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}'
)
DONE = "[DONE]"


def frames(*chunks: str) -> bytes:
    """Wrap raw payloads as SSE frames."""
    return "".join(f"data: {chunk}\n\n" for chunk in chunks).encode()


def content(text: str) -> str:
    """Build one content delta."""
    escaped = text.replace('"', '\\"')
    return (
        '{"choices":[{"index":0,"delta":{"content":"'
        + escaped
        + '"},"finish_reason":null}]}'
    )


def reply(text: str) -> bytes:
    """Build a complete, well-formed stream for a single reply."""
    return frames(ROLE_CHUNK, content(text), DONE)
