# aigrader/llm/__init__.py
"""
LLM package.

Keep all model-provider specifics in this package so the rest of the codebase
stays provider-agnostic.
"""

from .llm_client import LLMClient, LLMResponse

__all__ = ["LLMClient", "LLMResponse"]
