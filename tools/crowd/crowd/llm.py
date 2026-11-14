"""Sonnet through the Hostinger AI Router, one streamed tool call per decision.

The router reports `credits_charged` in each streamed usage block, the message_delta one being the total
for the call. When a response lacks it, the cost is estimated from tokens at the rates below, measured on
the router for claude-sonnet-5.5 (90 output and 13 input tokens charged 0.108 credits, in Sonnet's 5:1
output:input price ratio).
"""

import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass

import anthropic

ROUTER = "https://router.hostinger.com"
MODEL = "claude-sonnet-5.5"
CREDITS_PER_INPUT_TOKEN = 0.00023
CREDITS_PER_OUTPUT_TOKEN = 0.00117
RETRIES = 4
# Rate limits, overload and other server errors, and dropped connections.
BUSY = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)

log = logging.getLogger(__name__)


@dataclass
class Call:
    input: dict | None
    credits: float


class Llm:
    def __init__(self, model: str = MODEL, concurrency: int = 16):
        key = os.environ.get("HOSTINGER_ROUTER_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("neither HOSTINGER_ROUTER_KEY nor ANTHROPIC_API_KEY is set")
        # call() retries a busy router itself, waiting outside the concurrency slots.
        self.client = anthropic.Anthropic(
            api_key=key, base_url=os.environ.get("CROWD_ROUTER_URL", ROUTER), max_retries=0
        )
        self.model = model
        self.slots = threading.BoundedSemaphore(concurrency)

    def call(self, system: str, user: dict, tool: dict, max_tokens: int = 1200) -> Call:
        """Ask for one call of the tool; the tool input is None when the model answered without it.
        At most `concurrency` calls run at once, and one the router turns away or drops is retried with
        jittered exponential backoff."""
        for attempt in range(RETRIES + 1):
            try:
                with self.slots:
                    return self._call(system, user, tool, max_tokens)
            except BUSY as err:
                if attempt == RETRIES:
                    raise
                delay = 2 ** (attempt + 1) * random.uniform(0.5, 1.5)
                log.warning("router: %s; retrying in %.0f s", type(err).__name__, delay)
                time.sleep(delay)
        raise AssertionError("unreachable")

    def _call(self, system: str, user: dict, tool: dict, max_tokens: int) -> Call:
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
