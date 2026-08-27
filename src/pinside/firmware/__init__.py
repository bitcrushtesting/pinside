"""Firmware generation: a fixture config in, a buildable and testable project out."""

from .generate import GenerationError, Result, config_hash, generate

__all__ = ["generate", "config_hash", "GenerationError", "Result"]
