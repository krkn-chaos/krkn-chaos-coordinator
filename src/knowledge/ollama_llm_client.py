"""Custom Graphiti LLM client for Ollama.

Replaces `responses.parse` (OpenAI Responses API) with
`chat.completions.create` + JSON mode, which Ollama supports.
"""

import json
import logging
from typing import ClassVar

import openai
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_base_client import (
    BaseOpenAIClient,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REASONING,
    DEFAULT_VERBOSITY,
)

logger = logging.getLogger(__name__)


def _fix_field_names(data: dict | list, model: type[BaseModel] | None = None) -> dict | list:
    """Fix common field name mismatches from local models.

    Local models often use slightly different field names:
    - entity_name → name
    - entity_type → type
    - entity_type_id → type_id
    """
    remap = {
        "entity_name": "name",
        "entity_id": "name",
        "text": "name",
        "entity_text": "name",
        "label": "name",
        "entity_label": "name",
        "substring": "name",
        "entity_type": "type",
        "entity_description": "description",
        "desc": "description",
    }

    if isinstance(data, list):
        return [_fix_field_names(item, model) for item in data]

    if isinstance(data, dict):
        fixed = {}
        for key, value in data.items():
            new_key = remap.get(key, key)
            if isinstance(value, (dict, list)):
                fixed[new_key] = _fix_field_names(value, None)
            else:
                fixed[new_key] = value

        # If model expects 'name' and we have it from remap, good
        # If model expects fields we're missing, try to provide defaults
        if model is not None:
            schema = model.model_json_schema()
            for field in schema.get("required", []):
                if field not in fixed:
                    fixed[field] = ""
        return fixed

    return data


class _FakeResponse:
    """Mimics the OpenAI response object that Graphiti expects."""

    def __init__(self, parsed: BaseModel, text: str):
        self.output = [_FakeOutput(parsed, text)]
        self.output_text = text
        self.usage = _FakeUsage()


class _FakeOutput:
    def __init__(self, parsed: BaseModel, text: str):
        self.content = [_FakeContent(parsed, text)]


class _FakeContent:
    def __init__(self, parsed: BaseModel, text: str):
        self.parsed = parsed
        self.text = text


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0


class OllamaClient(BaseOpenAIClient):
    """Graphiti LLM client for Ollama."""

    PROVIDER_NAME: ClassVar[str] = "ollama"

    def __init__(
        self,
        config: LLMConfig | None = None,
        cache: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        reasoning: str | None = DEFAULT_REASONING,
        verbosity: str | None = DEFAULT_VERBOSITY,
    ):
        if config is None:
            config = LLMConfig(
                api_key="not-needed",
                model="qwen2.5-coder:14b",
                base_url="http://localhost:11434/v1",
            )
        super().__init__(
            config=config, cache=cache,
            max_tokens=max_tokens, reasoning=reasoning, verbosity=verbosity,
        )
        # Override the client to point at Ollama
        self.client = openai.AsyncOpenAI(
            api_key=config.api_key or "not-needed",
            base_url=config.base_url or "http://localhost:11434/v1",
        )

    async def _create_structured_completion(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float | None,
        max_tokens: int,
        response_model: type[BaseModel],
        reasoning: str | None = None,
        verbosity: str | None = None,
    ):
        """Use chat completions + JSON mode instead of responses.parse."""
        schema = response_model.model_json_schema()
        required = schema.get("required", [])
        props = schema.get("properties", {})

        # Build example with placeholder values instead of raw schema
        example = {}
        for fname, finfo in props.items():
            ftype = finfo.get("type", "string")
            if ftype == "string":
                example[fname] = "fill this in"
            elif ftype == "integer":
                example[fname] = 0
            elif ftype == "boolean":
                example[fname] = True
            elif ftype == "array":
                example[fname] = []
            else:
                example[fname] = "fill this in"

        fields_list = ", ".join(f'"{f}"' for f in (required or props.keys()))
        example_str = json.dumps(example, indent=2)

        msgs = list(messages)
        if msgs:
            last = dict(msgs[-1])
            last["content"] = (
                (last.get("content") or "")
                + f"\n\nRespond with a JSON object containing fields: {fields_list}. "
                + f"Example format:\n{example_str}\n"
                + f"Replace placeholder values with actual content based on the text above. Return ONLY the JSON."
            )
            msgs[-1] = last

        response = await self.client.chat.completions.create(
            model=model,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature or 0.1,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        content = content.strip()

        # Clean markdown wrapping
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
            content = content.strip()

        # Fix common field name mismatches from local models
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON from Ollama: {content[:200]}")

        data = _fix_field_names(data, response_model)
        fixed_content = json.dumps(data)

        # Validate
        parsed = response_model.model_validate(data)
        return _FakeResponse(parsed, fixed_content)

    async def _create_completion(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float | None,
        max_tokens: int,
        response_model: type[BaseModel] | None = None,
        reasoning: str | None = None,
        verbosity: str | None = None,
    ):
        """Regular chat completion."""
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature or 0.1,
        )
        return response
