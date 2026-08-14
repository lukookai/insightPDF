"""Offline-only translation helpers.

This package deliberately has no HTTP client dependency.  The PDF pipeline
uses it under a socket guard so document text cannot leave the machine.
"""

from .translator import OfflineCTranslate2Translator

__all__ = ["OfflineCTranslate2Translator"]
