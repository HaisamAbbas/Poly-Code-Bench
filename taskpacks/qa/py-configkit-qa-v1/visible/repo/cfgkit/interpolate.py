"""Environment interpolation for loaded configuration values."""

import re

from .errors import ConfigError

_REFERENCE = re.compile(r"(\\?)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def interpolate(value, environ):
    """Expand ``${NAME}`` references in one value against ``environ``.

    A backslash-escaped ``\\${NAME}`` stays the literal text ``${NAME}``: exactly
    1 backslash is consumed and no lookup happens. An unescaped reference whose
    name is missing from ``environ`` raises ``ConfigError`` naming the variable.
    """

    def replace(match):
        escaped, name = match.group(1), match.group(2)
        if escaped:
            return "${" + name + "}"
        if name not in environ:
            raise ConfigError(f"missing environment variable: {name}")
        return environ[name]

    return _REFERENCE.sub(replace, value)
