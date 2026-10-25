"""Token usage and estimated cost of one question (blueprint §13.1 "embedding and LLM token counts, estimated
inference cost per question", §13.2 trace shape).

One `TokenUsage` per request lives on the run context; the embedder and chat model add to it, and `persist`
stores it on the answer. Prices come from settings (0 for the fake providers), so the cost is an estimate from
list prices, not a bill.
"""

from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass(slots=True)
class TokenUsage:
    embedding_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0

    def add_llm(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += max(0, prompt)
        self.completion_tokens += max(0, completion)
        self.llm_calls += 1

    def cost_usd(self, settings: Settings) -> float:
        return round(
            self.embedding_tokens / 1000 * settings.embedding_price_per_1k_tokens
            + self.prompt_tokens / 1000 * settings.llm_price_per_1k_input_tokens
            + self.completion_tokens / 1000 * settings.llm_price_per_1k_output_tokens,
            6,
        )

    def as_record(self, settings: Settings) -> dict[str, Any]:
        return {
            "embedding": self.embedding_tokens,
            "prompt": self.prompt_tokens,
            "completion": self.completion_tokens,
            "llm_calls": self.llm_calls,
            "estimated_cost_usd": self.cost_usd(settings),
        }
