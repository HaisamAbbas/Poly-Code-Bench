# histkit

Small changelog parsing and rendering used by the release tooling.

```python
from histkit import parse_entries, render_summary

print(render_summary(parse_entries(open("HISTORY.md").read())))
```

See `CONTRIBUTING.md` before changing anything under `histkit/`.
