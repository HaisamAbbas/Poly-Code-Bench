"""Environment interpolation for loaded configuration values (scanner form)."""

from .errors import ConfigError


def _expand(value, environ):
    out = []
    index = 0
    while index < len(value):
        if value.startswith("\\${", index):
            end = value.find("}", index)
            if end == -1:
                out.append(value[index:])
                break
            out.append(value[index + 1 : end + 1])
            index = end + 1
            continue
        if value.startswith("${", index):
            end = value.find("}", index)
            if end == -1:
                out.append(value[index:])
                break
            name = value[index + 2 : end]
            if name not in environ:
                raise ConfigError(f"missing environment variable: {name}")
            out.append(environ[name])
            index = end + 1
            continue
        out.append(value[index])
        index += 1
    return "".join(out)


def resolve_sections(sections, environ):
    """Resolve every section value in place and return the sections."""
    for values in sections.values():
        for key in values:
            values[key] = _expand(values[key], environ)
    return sections
