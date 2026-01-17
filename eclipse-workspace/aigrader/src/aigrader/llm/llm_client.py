"""
aigrader.llm.llm_client

Thin wrapper around the OpenAI Responses API.

Design goals:
- Keep all OpenAI SDK usage in one place.
- Return the model's raw text (expected to be JSON) plus lightweight metadata.
- Raise aigrader.exceptions.LLMError on failures.

Env vars supported:
- OPENAI_API_KEY (required unless you pass api_key)
- OPENAI_MODEL (default: gpt-4o-2024-08-06)
- OPENAI_TIMEOUT_SECONDS (default: 60)
- OPENAI_MAX_RETRIES (default: 2)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openai import OpenAI

from aigrader.exceptions import LLMError


@dataclass(frozen=True)
class LLMResponse:
    """What the rest of the system needs from the model call."""
    text: str
    response_id: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class LLMClient:
    """
    Client for calling an OpenAI model to produce a rubric-scored JSON assessment.

    Usage:
        llm = LLMClient()
        resp = llm.generate(system_prompt=..., user_prompt=...)
        raw_json = resp.text
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        organization: Optional[str] = None,
        project: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise LLMError("Missing OPENAI_API_KEY (env var) or api_key parameter.")

        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-2024-08-06")

        self.timeout_seconds = float(timeout_seconds or os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))
        self.max_retries = int(max_retries or os.getenv("OPENAI_MAX_RETRIES", "2"))

        # OpenAI() reads OPENAI_API_KEY by default, but we pass explicitly for clarity.
        self._client = OpenAI(
            api_key=self.api_key,
            organization=organization,
            project=project,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: Optional[str] = None,
        temperature: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """
        Call the model and return its *text output*.

        Notes:
        - We request "json_object" formatting to strongly encourage valid JSON.
        - You already enforce strict JSON via your prompts; this is an extra guardrail.
        - For grading, you may prefer lower temperature for consistency.
        """
        if not system_prompt.strip():
            raise LLMError("system_prompt is empty.")
        if not user_prompt.strip():
            raise LLMError("user_prompt is empty.")

        payload: Dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Ask for JSON output at the API level (pairs well with your rubric JSON schema prompt).
            "text": {"format": {"type": "json_object"}},
        }

        # Optional controls (supported across many models; harmless if ignored by some).
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}  # e.g., "low", "medium", "high"
        if temperature is not None:
            payload["temperature"] = float(temperature)

        if extra:
            # Allow caller to pass advanced parameters without changing this API surface.
            payload.update(extra)

        try:
            resp = self._client.responses.create(**payload)
        except Exception as e:
            raise LLMError(f"OpenAI API call failed: {e}") from e

        # OpenAI SDK convenience: output_text aggregates the text output.
        text = getattr(resp, "output_text", None)
        if not text or not str(text).strip():
            raise LLMError("Model returned empty output_text.")

        usage = None
        try:
            usage_obj = getattr(resp, "usage", None)
            if usage_obj is not None:
                # usage is an object; convert to dict-ish safely
                usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)
        except Exception:
            usage = None

        response_id = getattr(resp, "id", None)
        model = getattr(resp, "model", None) or self.model

        return LLMResponse(
            text=str(text).strip(),
            response_id=response_id,
            model=model,
            usage=usage,
        )
