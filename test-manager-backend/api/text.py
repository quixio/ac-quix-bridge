"""Shared text-normalization helpers.

`fold_for_lookup` is the accent/case-insensitive fold used to match driver
names against the lake. `driver_name_key` layers whitespace collapsing on top
of it to produce the stored uniqueness / join key for the Driver entity.
"""

import unicodedata


def fold_for_lookup(name: str) -> str:
    """Fold a name to an accent- and case-insensitive ASCII key.

    NFKD-normalize, drop non-ASCII (accents), lowercase. Falls back to a plain
    lowercase when folding would empty the string (e.g. an all-CJK name).
    """
    if not name:
        return ""
    folded = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return folded or name.lower()


def driver_name_key(name: str) -> str:
    """Folded uniqueness/lookup key for a driver name.

    Collapses surrounding and internal whitespace before folding so
    "Petr  Cech" and " Petr Cech " resolve to the same driver. This is both
    the dedup key (unique index) and the Test.driver -> Driver join key.
    """
    return fold_for_lookup(" ".join(name.split()))
