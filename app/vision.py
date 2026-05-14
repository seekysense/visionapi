from __future__ import annotations

import json
import re

import openai

from app.axis import resize_image, to_b64
from app.config import get_settings

# On HTTP 500 from LLM, retry with progressively smaller images.
_RETRY_SCALES = [1.0, 0.75, 0.5, 0.25]


class ImageTooLargeError(Exception):
    """Raised when LLM returns 500 on all resize attempts (images exceed context window)."""


def _img_block(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _build_messages(frames: list[bytes], prompt: str) -> list[dict]:
    content: list[dict] = []
    if len(frames) == 1:
        content.append(_img_block(to_b64(frames[0])))
    else:
        labels = [
            "Frame 1 (oldest):",
            "Frame 2:",
            "Frame 3:",
            "Frame 4 (most recent):",
        ]
        for i, frame in enumerate(frames):
            label = labels[i] if i < len(labels) else f"Frame {i + 1}:"
            content.append({"type": "text", "text": label})
            content.append(_img_block(to_b64(frame)))
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


def _extract_json_object(text: str) -> str:
    """
    Return the first balanced {...} block found in text.
    Handles nested objects, strings with escaped quotes, and surrounding prose.
    Raises ValueError if no complete object is found.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("No '{' found in LLM response")

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unbalanced braces in LLM response — JSON object is incomplete")


def _get_message_text(message) -> str:
    """
    Extract the text content from a chat completion message.
    Handles three formats produced by reasoning models:
      1. content is a plain string (standard)
      2. content is a list of blocks — extract the first type=text block
      3. content is None but model_extra["content"] has blocks (extended thinking via OpenAI SDK)
    Raises ValueError if no text is found.
    """
    content = message.content

    if isinstance(content, str) and content:
        return content

    # List of blocks (e.g. Anthropic extended thinking via some proxies)
    if isinstance(content, list):
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            btext = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if btype == "text" and btext:
                return btext

    # content=None: check model_extra (OpenAI SDK stores unknown fields there)
    extra = getattr(message, "model_extra", None) or {}
    extra_content = extra.get("content", [])
    if isinstance(extra_content, list):
        for block in extra_content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    return text

    # Some endpoints put the answer inside the reasoning field when enable_thinking=True
    reasoning = extra.get("reasoning", "")
    if isinstance(reasoning, str) and reasoning:
        return reasoning

    raise ValueError(
        f"LLM returned no text content. "
        f"content={content!r}, model_extra keys={list(extra.keys()) if extra else []}"
    )


def _strip_markdown_fence(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers if present."""
    text = text.strip()
    # Match optional language tag after opening fence
    m = re.match(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def extract_and_validate_json(raw: str) -> dict:
    """
    Sanitise LLM output and return a validated JSON object (dict).
    Steps:
      1. Strip markdown code fences
      2. Extract first balanced {} block (ignores surrounding prose)
      3. Parse as JSON
      4. Assert the result is a dict, not a list or scalar
    Raises ValueError with a descriptive message on any failure.
    """
    cleaned = _strip_markdown_fence(raw)
    try:
        candidate = _extract_json_object(cleaned)
    except ValueError as e:
        raise ValueError(f"{e}. Raw response (first 300 chars): {raw[:300]!r}") from None

    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Extracted block is not valid JSON ({e}). "
            f"Block (first 300 chars): {candidate[:300]!r}"
        ) from None

    if not isinstance(result, dict):
        raise ValueError(
            f"LLM returned valid JSON but not an object (got {type(result).__name__}): "
            f"{candidate[:300]!r}"
        )

    return result


async def analyze(frames: list[bytes], prompt: str) -> dict:
    s = get_settings()
    client = openai.AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key)

    last_err: Exception | None = None
    for i, scale in enumerate(_RETRY_SCALES):
        scaled = [resize_image(f, scale) for f in frames] if scale < 1.0 else list(frames)
        messages = _build_messages(scaled, prompt)

        try:
            resp = await client.chat.completions.create(
                model=s.llm_model,
                messages=messages,
                temperature=0.1,
                max_tokens=1024,
                timeout=s.llm_timeout,
                extra_body={"enable_thinking": s.llm_reasoning},
            )
            return extract_and_validate_json(_get_message_text(resp.choices[0].message))

        except openai.APIStatusError as e:
            if e.status_code != 500:
                raise
            last_err = e
            if i < len(_RETRY_SCALES) - 1:
                continue  # retry with smaller images

    scales_pct = [f"{int(sc * 100)}%" for sc in _RETRY_SCALES]
    raise ImageTooLargeError(
        f"LLM returned HTTP 500 on all {len(_RETRY_SCALES)} attempts "
        f"(scales tried: {', '.join(scales_pct)}). "
        f"Images likely exceed model context window ({s.llm_context_window} tokens)."
    ) from last_err


async def analyze_sequence_final(
    chunk_results: list[dict],
    final_prompt: str,
    batch_duration_s: float,
) -> dict:
    """Chiamata LLM testo-only per la sintesi finale dei risultati chunk. Non invia immagini."""
    s = get_settings()
    client = openai.AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key)

    filled_prompt = (
        final_prompt
        .replace("{chunk_results}", json.dumps(chunk_results, indent=2, ensure_ascii=False))
        .replace("{batch_duration_s}", f"{batch_duration_s:.1f}")
    )

    resp = await client.chat.completions.create(
        model=s.llm_model,
        messages=[{"role": "user", "content": filled_prompt}],
        temperature=0.1,
        max_tokens=1024,
        timeout=s.llm_timeout,
        extra_body={"enable_thinking": s.llm_reasoning},
    )
    return extract_and_validate_json(_get_message_text(resp.choices[0].message))
