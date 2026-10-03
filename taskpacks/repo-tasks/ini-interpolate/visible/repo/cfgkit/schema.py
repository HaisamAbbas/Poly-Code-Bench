"""Light structural validation for parsed sections."""

from .errors import ConfigError


def validate(sections):
    """Reject shapes our callers cannot consume; return the sections unchanged."""
    if "" in sections and len(sections) > 1:
        raise ConfigError("top-level keys must appear before any section")
    for name, values in sections.items():
        for key in values:
            if not key.replace("-", "_").isidentifier():
                raise ConfigError(f"invalid key: {key}")
    return sections
