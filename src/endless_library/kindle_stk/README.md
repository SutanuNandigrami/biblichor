# kindle_stk — biblichor's Send-to-Kindle integration

Wraps a vendored copy of [maxdjohnson/stkclient](https://github.com/maxdjohnson/stkclient)
(MIT) with biblichor's encrypted secrets store, exception hierarchy,
and rate-limit gate.

## What's in here

- `_vendored/` — verbatim copy of stkclient v0.1.1 source, files
  prefixed with `_` so it's obvious which code is upstream. **Do
  not modify these files directly** — sync via `tools/sync_stkclient.py`
  if the upstream gets a bugfix release worth pulling.
- `__init__.py` — biblichor's public surface
- `service.py` — `KindleStkService` façade
- `exceptions.py` — biblichor's typed exceptions over stkclient's raw ones

## Sync upstream

```bash
python tools/sync_stkclient.py            # current pinned version
python tools/sync_stkclient.py --tag X.Y  # specific tag
```

Re-run `python -m pytest tests/unit/test_kindle_stk_*.py` after.

## License

stkclient is MIT-licensed. See `LICENSE.stkclient` for the full text.
biblichor's code in this directory is under the same license as the
rest of the project.
