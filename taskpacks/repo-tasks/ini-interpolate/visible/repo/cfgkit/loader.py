"""INI-style configuration loading for cfgkit."""

from .errors import ConfigError
from .schema import validate


def parse(text):
    """Parse INI text into ``{section: {key: raw value}}``; values stay raw."""
    sections = {}
    current = ""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" not in stripped:
            raise ConfigError(f"line {number}: expected key = value")
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            raise ConfigError(f"line {number}: empty key")
        sections.setdefault(current, {})[key] = value.strip()
    return sections


def load(text, environ):
    """Load a config with its values resolved against *environ*."""
    return validate(parse(text))
