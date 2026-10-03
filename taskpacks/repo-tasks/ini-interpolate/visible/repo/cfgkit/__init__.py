"""cfgkit: small INI-style configuration loading."""

from .errors import ConfigError
from .loader import load, parse

__all__ = ["ConfigError", "load", "parse"]
