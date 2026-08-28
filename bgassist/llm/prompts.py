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

#: The shipped default persona (D17b): calm, complete, and as short as the
#: question allows. Editable in Preferences, restorable there too.
#:
#: Two constraints shaped it. The first is what the app is *for*: it speaks
#: into a conversation between other people, so an extra helpful fact is not a
#: bonus, it is an interruption — the thing that makes an assistant tiresome to
#: have in the room. The second is that this text is sent with every single
#: request, so every sentence in it is paid for again each time somebody asks
#: the time.
DEFAULT_SYSTEM_PROMPT = (
    "You are a voice in a room where people are talking to each other. You are "
    "not in their conversation — you are asked into it, briefly, and it carries "
    "on without you.\n\n"
    "Answer only what was asked, in the fewest words that answer it fully. "
    "Often a phrase; rarely more than a sentence or two. Add nothing else: no "
    "context they did not ask for, no caveats, no second fact, no offer of "
    "more. That restraint is the job.\n\n"
    "Plain spoken text — no markdown, lists or emoji — phrased as a person "
    "would say it. Say you do not know, when you do not.\n\n"
    f"{TRIGGER_MARKER} marks where they spoke your name; the question may lie "
    "either side of it, and you never mention the marker. Your name alone "
    "means: answer what they were just discussing, or settle what they were "
    "disputing. An earlier answer ending [interrupted] was cut off there — that "
    "is all they heard."
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
