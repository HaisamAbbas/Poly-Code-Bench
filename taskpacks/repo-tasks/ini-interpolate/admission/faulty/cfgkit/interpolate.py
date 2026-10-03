"""Environment interpolation for loaded configuration values."""

import re

from .errors import ConfigError

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def interpolate(value, environ):
    """Expand ``${NAME}`` references in one value."""

    def replace(match):
        name = match.group(1)
        if name not in environ:
            raise ConfigError(f"missing environment variable: {name}")
        return environ[name]

    return _REFERENCE.sub(replace, value)
