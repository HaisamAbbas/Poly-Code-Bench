"""Environment interpolation for loaded configuration values."""

import re

from .errors import ConfigError

_REFERENCE = re.compile(r"(\\?)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def interpolate(value, environ):
    """Expand ``${NAME}`` references in one value; ``\\${NAME}`` stays literal."""

    def replace(match):
        escaped, name = match.group(1), match.group(2)
        if escaped:
            return "${" + name + "}"
        if name not in environ:
            raise ConfigError(f"missing environment variable: {name}")
        return environ[name]

    return _REFERENCE.sub(replace, value)
