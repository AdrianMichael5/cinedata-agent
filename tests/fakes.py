"""Test doubles for the agent: a scripted LLM that never touches the network."""

import copy
import json
from collections.abc import Sequence
from typing import Any

from openai.types.chat import ChatCompletionMessage

from cinedata_agent.llm.client import LLMResponse

FAKE_MODEL = "fake/model:free"


def text_message(content: str) -> ChatCompletionMessage:
    return ChatCompletionMessage.model_validate({"role": "assistant", "content": content})


def tool_call_message(*queries: str, raw_arguments: str | None = None) -> ChatCompletionMessage:
    """Assistant message calling run_sql once per query (or once with raw_arguments)."""
    if raw_arguments is not None:
        arguments = [raw_arguments]
    else:
        arguments = [json.dumps({"query": query}) for query in queries]
    return tool_calls_message([("run_sql", argument) for argument in arguments])


def tool_calls_message(calls: Sequence[tuple[str, str]]) -> ChatCompletionMessage:
    return ChatCompletionMessage.model_validate(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
                for index, (name, arguments) in enumerate(calls, start=1)
            ],
        }
    )


class FakeLLM:
    """Returns scripted messages in order; an Exception in the script is raised instead."""

    def __init__(
        self, script: Sequence[ChatCompletionMessage | Exception], model: str = FAKE_MODEL
    ) -> None:
        self.script = list(script)
        self.model = model
        self.calls: list[list[dict[str, Any]]] = []
        self.tools: list[list[Any]] = []

    @property
    def requests_sent(self) -> int:
        return len(self.calls)

    def complete(self, messages: Sequence[Any], tools: Sequence[Any]) -> LLMResponse:
        self.calls.append(copy.deepcopy(list(messages)))
        self.tools.append(list(tools))
        if not self.script:
            raise AssertionError("FakeLLM was called more times than scripted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            message=item, model_used=self.model, requested_model=self.model, attempts=()
        )
