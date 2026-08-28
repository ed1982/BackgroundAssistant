"""System prompt and message construction, including the trigger marker (D8).

Rather than slicing the transcript ourselves to guess which half was the
question — the string surgery that produced F16 — we hand the model the
utterance with the trigger position marked and let it work out the intent.
That is robust to phrasings nobody anticipated and costs nothing extra: it is
the same request either way.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from bgassist.core.trigger import TRIGGER_MARKER

#: The calm ship's-computer persona shipped as the default (D17b). This is
#: behaviour, not branding — it is fully editable in Preferences and the
#: default can be restored there.
DEFAULT_SYSTEM_PROMPT = (
    "You are a calm, precise ship's computer. You answer questions asked aloud "
    "in a room, so answer in one to three short sentences that sound natural "
    "when spoken. Use plain text only: no markdown, no bullet points, no code "
    "blocks and no emoji. Be measured and factual; say plainly when you do not "
    "know something.\n\n"
    "The user addresses you by name. The marker "
    f"{TRIGGER_MARKER} shows where in their speech they said it. What they are "
    "asking may come before the marker, after it, or on both sides. Work out "
    "the actual question and answer that — never comment on the marker itself. "
    "You are also given the recent conversation in the room as context. People "
    "often say your name and nothing else, which means: answer the question "
    "they were just asking each other, or settle the point they were disputing. "
    "Do that directly — do not ask them to repeat it, and do not summarise the "
    "conversation back at them. Only ask what they meant if the discussion "
    "genuinely contained no question at all.\n\n"
    "An assistant turn ending in [interrupted] is one the user cut you off in: "
    "they only heard the part shown, so continue from there rather than "
    "assuming you finished."
)

#: Suffix appended to a truncated assistant turn in history (§5.4.1, D12a).
INTERRUPTED_SUFFIX = " [interrupted]"

TITLE_PROMPT = (
    "Give a three to five word title for this exchange. Reply with the title "
    "only: no quotes, no punctuation at the end, no explanation."
)


def build_messages(context_text: str = "", query: str = "",
                   history: Optional[Sequence[Dict[str, str]]] = None,
                   system_prompt: Optional[str] = None,
                   marked_utterance: str = "") -> List[Dict[str, str]]:
    """Build the chat message list.

    *history* is prior turns of this conversation (F9), already truncated to
    the spoken prefix for any interrupted assistant turn. *marked_utterance* is
    the triggering utterance with :data:`TRIGGER_MARKER` in place of the
    trigger word; when it is absent we fall back to the plain *query*.
    """
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}
    ]
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    parts: List[str] = []
    if context_text and context_text.strip():
        parts.append("Recent conversation in the room (oldest first, times are "
                     f"local):\n{context_text.strip()}")
    spoken = (marked_utterance or "").strip()
    if spoken:
        parts.append(f"They just said: {spoken}")
    elif query and query.strip():
        parts.append(f"The user just said: {query.strip()}")
    else:
        # The flagship interaction: they said the name and stopped. The point
        # of hearing the room is that this is answerable without asking them
        # to say it all again.
        parts.append("They said your name and nothing else. Answer the "
                     "question they were just asking each other, or settle the "
                     "point they were disputing, using the conversation above.")
    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return messages


def history_from_messages(messages: Sequence) -> List[Dict[str, str]]:
    """Turn stored :class:`~bgassist.storage.conversations.Message` rows into
    chat turns, sending only what the user actually heard (D12a).

    This is the rule that keeps the model's picture of the exchange and the
    user's picture identical: an interrupted answer is replayed as its spoken
    prefix with an ``[interrupted]`` marker, never as the full generated text.
    A question that was superseded before anything was spoken is not replayed
    at all (§5.4.4).
    """
    turns: List[Dict[str, str]] = []
    for message in messages:
        role = getattr(message, "role", None)
        if getattr(message, "superseded", False):
            continue
        text = getattr(message, "text", "") or ""
        if role == "assistant" and getattr(message, "interrupted", False):
            upto = getattr(message, "spoken_upto", None)
            prefix = text[:upto] if upto is not None else text
            text = prefix.rstrip() + INTERRUPTED_SUFFIX
        if role in ("user", "assistant") and text.strip():
            turns.append({"role": role, "content": text})
    return turns
