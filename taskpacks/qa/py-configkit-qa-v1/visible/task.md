# cfgkit: environment references in load()

How does cfgkit load() resolve environment references, and what happens when a
referenced variable is missing? Answer with claims citing exact spans of the
snapshot files.

The snapshot is the `cfgkit/` package next to this task: `cfgkit/loader.py`,
`cfgkit/interpolate.py`, `cfgkit/errors.py` and `cfgkit/__init__.py`. Read them
and answer from what reading and search return; nothing may be edited.

## Answer format

The answer must be one JSON envelope and nothing else:

```json
{"answer": "<prose answer>", "claims": [{"text": "<claim quoting the answer>", "citations": [{"path": "cfgkit/<file>.py", "start_line": 1, "end_line": 2, "base_digest": "sha256:..."}]}]}
```

- `answer` is your prose answer; each claim's `text` must quote that answer text.
- Every citation points at one exact span of a snapshot file: `path`,
  `start_line`, `end_line` and `base_digest`.
- Citations are tied to the pinned base snapshot digest and must carry exactly
  `sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08`.
