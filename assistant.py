"""Claude-powered call analysis: troubleshooting steps, parts, and ticket fields,
grounded in knowledge-base articles retrieved for the current conversation."""
from __future__ import annotations

import json
import logging

import anthropic

from .config import settings
from .kb import Article, KnowledgeBase

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a real-time co-pilot for a Lenovo hardware & software technical support agent \
(ThinkPad, ThinkStation, ThinkCentre, IdeaPad, monitors and docks). You listen to the live call transcript \
and help the agent resolve the issue quickly and accurately.

Rules:
- Ground troubleshooting and parts in the KNOWLEDGE BASE ARTICLES provided. Cite article ids in `kb_refs`.
- If the KB does not cover something, you may use general Lenovo/PC support knowledge, but set \
`source` to "general" for that step and keep confidence honest. Never invent part numbers (FRUs): \
only give a FRU/part number if it appears in a KB article; otherwise describe the part and leave `part_number` empty.
- Order troubleshooting from least to most invasive (e.g. power reset / drain, Lenovo Diagnostics, \
BIOS/driver via Lenovo Vantage or System Update, BIOS rollback, then hardware replacement).
- Recommend parts only when the evidence supports a hardware fault; explain why in `reason`.
- Extract customer details ONLY if they were actually said on the call. Never guess names, emails, phones or serials. \
Normalise spelled-out emails ("john dot smith at gmail dot com" -> john.smith@gmail.com) and phone numbers.
- Distinguish the agent from the customer by context (the agent greets, asks questions, reads steps).
- Keep every item short enough to read at a glance during a live call.
- Suggest the next 1-3 questions the agent should ask to narrow the fault or confirm warranty/entitlement."""

ANALYSIS_TOOL = {
    "name": "report_call_analysis",
    "description": "Return the structured analysis of the call so far.",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer": {
                "type": "object",
                "properties": {
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "full_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                },
            },
            "device": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "e.g. ThinkPad T14 Gen 3"},
                    "machine_type": {"type": "string", "description": "4-char MT, e.g. 21AH"},
                    "serial_number": {"type": "string"},
                    "os": {"type": "string"},
                    "warranty_note": {"type": "string"},
                },
            },
            "reason_for_call": {"type": "string", "description": "One-line case title, e.g. 'T14 Gen 3 no power after BIOS update'"},
            "issue_summary": {"type": "string", "description": "2-4 sentence case description for the ticket"},
            "category": {"type": "string", "enum": ["hardware", "software", "bios_firmware", "os", "warranty_order", "accessory", "other"]},
            "troubleshooting_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string"},
                        "why": {"type": "string"},
                        "source": {"type": "string", "enum": ["kb", "general"]},
                    },
                    "required": ["step", "source"],
                },
            },
            "parts_needed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "part": {"type": "string"},
                        "part_number": {"type": "string"},
                        "reason": {"type": "string"},
                        "likelihood": {"type": "string", "enum": ["confirmed", "likely", "possible"]},
                    },
                    "required": ["part", "reason", "likelihood"],
                },
            },
            "questions_to_ask": {"type": "array", "items": {"type": "string"}},
            "kb_refs": {"type": "array", "items": {"type": "string"}},
            "escalate": {"type": "boolean", "description": "True if this needs L2/engineering escalation"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["reason_for_call", "troubleshooting_steps", "parts_needed", "questions_to_ask", "confidence"],
    },
}


class CallAssistant:
    def __init__(self, kb: KnowledgeBase) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.kb = kb
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _retrieve(self, transcript: str, previous: dict | None) -> list[Article]:
        # Weight the latest part of the call, plus what we've already identified.
        recent = transcript[-2500:]
        extra = ""
        if previous:
            extra = " ".join([previous.get("reason_for_call", ""), json.dumps(previous.get("device", {}))])
        return [a for a, _ in self.kb.search(recent + " " + extra, k=4)]

    async def analyze(self, transcript: str, previous: dict | None = None) -> tuple[dict, list[Article]]:
        articles = self._retrieve(transcript, previous)
        kb_block = "\n\n".join(
            f"<article id=\"{a.id}\" title=\"{a.title}\">\n"
            + "".join(f"{k}: {v}\n" for k, v in a.meta.items() if k != "title")
            + a.snippet()
            + "\n</article>"
            for a in articles
        ) or "(no matching articles)"

        user = (
            f"KNOWLEDGE BASE ARTICLES:\n{kb_block}\n\n"
            f"PREVIOUS ANALYSIS (update it; keep details already confirmed):\n"
            f"{json.dumps(previous, indent=1) if previous else 'none'}\n\n"
            f"CALL TRANSCRIPT SO FAR:\n{transcript}"
        )
        resp = await self.client.messages.create(
            model=settings.claude_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=[ANALYSIS_TOOL],
            tool_choice={"type": "tool", "name": "report_call_analysis"},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input, articles
        raise RuntimeError("Model did not return an analysis")
