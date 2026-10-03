# cfgkit

Small INI-style configuration loading used by our deploy tooling.

```python
from cfgkit import load

sections = load(open("deploy.cfg").read(), os.environ)
```

See `CONTRIBUTING.md` before changing anything under `cfgkit/`.
