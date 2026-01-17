"""
Canvas integration package.

Houses the CanvasClient abstraction used by AIGrader.
"""

from .client import CanvasClient, CanvasAuth

__all__ = ["CanvasClient", "CanvasAuth"]
