"""cfgkit: small INI-style configuration loading with environment references."""

from .errors import ConfigError
from .interpolate import interpolate
from .loader import load, parse

__all__ = ["ConfigError", "interpolate", "load", "parse"]
