"""One-shot environment probe (throwaway diagnostic).

Dumps the container's runtime environment to stdout — specifically the tokens
Quix byox auto-injects (Lakehouse query/catalog URLs + auth tokens, blob storage
connection JSON) when a deployment has `blobStorage.bind: true`. Deployed as a
Quix Job so it runs once, prints, and exits without leaving a service running.

Reads only `os.environ`; no dependencies. Delete the deployment + this dir
(`env-probe/`) after reading the logs.
"""

from __future__ import annotations

import os
import sys

# A var is "relevant" if it starts with one of these (case-sensitive, matches
# Quix's mixed-case `Quix__...` names and UPPER snake-case legacy names) ...
PREFIXES = ("Quix__", "LAKE", "BLOB", "MONGO", "CATALOG", "S3", "AWS", "AZURE", "GCP")
# ... or contains one of these substrings (case-insensitive).
INTEREST = ("URL", "TOKEN", "PAT", "KEY", "SECRET", "CONNECTION", "PASSWORD", "BROKER", "LAKEHOUSE")


def _relevant(key: str) -> bool:
    ku = key.upper()
    return key.startswith(PREFIXES) or any(s in ku for s in INTEREST)


def main() -> None:
    env = dict(os.environ)
    bar = "=" * 72
    print(bar, flush=True)
    print(f"ENV-PROBE  |  {len(env)} environment variables in container", flush=True)
    print(bar, flush=True)

    # Full values for the vars we care about. This is a private dev workspace
    # and only the workspace owner can read Job logs, so values are unmasked —
    # the whole point is to discover the injected URLs + tokens.
    print("\n--- RELEVANT VARS (Quix / lakehouse / blob / mongo / creds) ---", flush=True)
    hits = [k for k in sorted(env) if _relevant(k)]
    if hits:
        for k in hits:
            print(f"{k} = {env[k]}", flush=True)
    else:
        print("(none matched — byox injected nothing matching the filter)", flush=True)

    # Names only for everything else, so we can spot anything the filter missed.
    print("\n--- ALL VAR NAMES ---", flush=True)
    print(", ".join(sorted(env)), flush=True)

    print(f"\n{bar}\nENV-PROBE  |  done\n{bar}", flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
