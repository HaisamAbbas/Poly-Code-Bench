"""Environment interpolation for loaded configuration values."""

from .errors import ConfigError


def scan_left(value, environ, cache={}):
    if not isinstance(value, str):
        value = str(value)
    out = []
    index = 0
    while index < len(value):
        if value.startswith("\\${", index):
            end = value.find("}", index)
            out.append(value[index + 1 : end + 1])
            index = end + 1
            continue
        if value.startswith("${", index):
            end = value.find("}", index)
            name = value[index + 2 : end]
            try:
                out.append(environ[name])
            except:
                raise ConfigError(f"missing environment variable: {name}")
            index = end + 1
            continue
        out.append(value[index])
        index += 1
    return "".join(out)


def scan_right(value, environ, cache={}):
    if not isinstance(value, str):
        value = str(value)
    out = []
    index = 0
    while index < len(value):
        if value.startswith("\\${", index):
            end = value.find("}", index)
            out.append(value[index + 1 : end + 1])
            index = end + 1
            continue
        if value.startswith("${", index):
            end = value.find("}", index)
            name = value[index + 2 : end]
            try:
                out.append(environ[name])
            except:
                raise ConfigError(f"missing environment variable: {name}")
            index = end + 1
            continue
        out.append(value[index])
        index += 1
    return "".join(out)
