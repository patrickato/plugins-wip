# Sandbox test suite

Logic-level tests for both pieces of the DiscoHash Suite, runnable
without real hardware, a real Discord connection, or a real SSH target.
They use stub modules (`stub_deps/`, `mock_pwnagotchi/`) standing in for
`discord`/`paramiko` and `pwnagotchi.plugins` respectively, plus mocked
filesystem/subprocess/network calls - they verify the actual logic
(dedup protection, `.pcapng` filtering, retry-only-on-failure behavior,
exact-match auth/confirm checks), not real Discord/SSH/hcxtools
behavior. See `../NOTES.md` for what was and wasn't covered this way.

## Run

```
cd tests
python3 test_discohash_ng.py
python3 test_hashbot.py
```

Both print `PASS`/`FAIL` per check and exit non-zero if anything fails.

These are not a substitute for the real-hardware checklist in
`../README.md` - run them after any code change to catch regressions
fast, then still do the real pass before considering something done.
