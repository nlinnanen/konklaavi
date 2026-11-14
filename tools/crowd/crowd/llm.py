"""Sonnet through the Hostinger AI Router, one streamed tool call per decision.

The router reports `credits_charged` in each streamed usage block, the message_delta one being the total
for the call. When a response lacks it, the cost is estimated from tokens at the rates below, measured on
the router for claude-sonnet-5.5 (90 output and 13 input tokens charged 0.108 credits, in Sonnet's 5:1
output:input price ratio).
"""

import json
import os
from dataclasses import dataclass

import anthropic

ROUTER = "https://router.hostinger.com"
MODEL = "claude-sonnet-5.5"
CREDITS_PER_INPUT_TOKEN = 0.00023
CREDITS_PER_OUTPUT_TOKEN = 0.00117


@dataclass
class Call:
    input: dict | None
    credits: float


class Llm:
    def __init__(self, model: str = MODEL):
        key = os.environ.get("HOSTINGER_ROUTER_KEY")
        if not key:
            raise SystemExit("HOSTINGER_ROUTER_KEY is not set")
        self.client = anthropic.Anthropic(api_key=key, base_url=os.environ.get("CROWD_ROUTER_URL", ROUTER))
        self.model = model

    def call(self, system: str, user: dict, tool: dict, max_tokens: int = 1200) -> Call:
        """Ask for one call of the tool; the tool input is None when the model answered without it."""
        credits = None
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            tools=[tool],
            thinking={"type": "disabled"},
        ) as stream:
            # The router returns an empty tool input to a non-streaming request, and current models
            # reject a forced tool_choice, so the system prompt asks for the call.
            for event in stream:
                if event.type == "message_delta":
                    credits = (event.usage.model_extra or {}).get("credits_charged", credits)
            message = stream.get_final_message()
        if credits is None:
            usage = message.usage
            tokens_in = (usage.input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
            credits = tokens_in * CREDITS_PER_INPUT_TOKEN + usage.output_tokens * CREDITS_PER_OUTPUT_TOKEN
        block = next((b for b in message.content if b.type == "tool_use"), None)
        return Call(block.input if block else None, float(credits))
