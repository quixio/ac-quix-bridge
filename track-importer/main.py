"""One-shot job: import bundled AC track-layout CSVs into Mongo track_layouts.

Run in-cloud with the deployment's injected MONGO_* secrets. One document per
layout, idempotent upsert keyed on `_id = "<track>/<layout>"`.

Usage:
    python -m main [--dry-run] [--data-dir ./data/tracks_csv]
                   [--database <name>] [--config-map ./config_map.json]
"""

import argparse
import json
import sys
from pathlib import Path

import importer
from settings import MongoSettings

DEFAULT_DATA_DIR = Path(__file__).parent / "data" / "tracks_csv"
DEFAULT_CONFIG_MAP = Path(__file__).parent / "config_map.json"
WARN_BYTES = int(importer.MAX_DOC_BYTES * importer.WARN_DOC_FRACTION)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse, validate, build docs and print the summary table but make "
        "no Mongo connection or writes.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Root of bundled track CSVs (default: {DEFAULT_DATA_DIR}).",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Override MONGO_DATABASE for this run.",
    )
    parser.add_argument(
        "--config-map",
        type=Path,
        default=DEFAULT_CONFIG_MAP,
        help="Optional JSON override map {'<track>/<layout>': '<ac_config>'}.",
    )
    return parser.parse_args(argv)


def load_config_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config map {path} must be a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def _fmt_config(value: str) -> str:
    return "(empty)" if value == "" else value


def run(args: argparse.Namespace) -> int:
    mode = "DRY-RUN" if args.dry_run else "LIVE"
    data_dir: Path = args.data_dir

    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 2

    config_map = load_config_map(args.config_map)
    if config_map:
        print(f"Loaded {len(config_map)} config_map override(s) from {args.config_map}")

    by_track = importer.discover_layouts(data_dir)
    imported_at = importer.utc_now()

    # Determine the target database name (for the banner) without connecting
    # in dry-run mode.
    if args.dry_run:
        database_name = args.database or "test_manager"
        collection = None
    else:
        settings_kwargs = {"database": args.database} if args.database else {}
        settings = MongoSettings(**settings_kwargs)  # type: ignore[arg-type]
        database_name = settings.database
        import mongo  # local import so dry-run never imports pymongo client path

        db = mongo.connect(settings)
        collection = db.track_layouts

    print(
        f"DATABASE: {database_name}   COLLECTION: track_layouts   MODE: {mode}"
    )
    if mode == "DRY-RUN":
        print("DRY RUN -- no writes")
    print(
        "NOTE: active database is "
        f"{database_name!r} (default test_manager; override via "
        "MONGO_DATABASE / --database)."
    )
    print()

    header = (
        f"{'track':<22}{'config(_id)':<34}{'trackConfiguration':<22}"
        f"{'points':>8}{'corners':>9}{'length_m':>11}{'doc_bytes':>12}  status"
    )
    print(header)

    rows: list[tuple] = []
    total_points = 0
    inserted = 0
    replaced = 0
    errors = 0
    skipped = 0
    max_doc_bytes = 0
    used_heuristic_any = False

    for track, layouts in by_track.items():
        layout_count = len(layouts)
        for layout, csv_path, corners_path in layouts:
            doc_id = f"{track}/{layout}"
            track_config, used_heuristic = importer.derive_track_configuration(
                track, layout, layout_count, config_map
            )
            used_heuristic_any = used_heuristic_any or used_heuristic
            try:
                doc = importer.build_document(
                    track,
                    layout,
                    csv_path,
                    corners_path,
                    track_config,
                    imported_at,
                )
                doc_bytes = importer.doc_bson_size(doc)
                max_doc_bytes = max(max_doc_bytes, doc_bytes)
                total_points += doc["n_points"]
                if doc_bytes >= WARN_BYTES:
                    print(
                        f"  WARNING: {doc_id} doc is {doc_bytes} bytes — "
                        f"approaching the {importer.MAX_DOC_BYTES}-byte limit",
                        file=sys.stderr,
                    )

                if args.dry_run:
                    status = "ok"
                else:
                    result = collection.replace_one(  # type: ignore[union-attr]
                        {"_id": doc_id}, doc, upsert=True
                    )
                    if result.upserted_id is not None:
                        status = "upserted"
                        inserted += 1
                    else:
                        status = "replaced"
                        replaced += 1
            except Exception as exc:  # noqa: BLE001 — report-and-continue per layout
                status = "error"
                errors += 1
                doc_bytes = 0
                print(f"  ERROR building {doc_id}: {exc}", file=sys.stderr)

            rows.append(
                (
                    track,
                    doc_id,
                    _fmt_config(track_config),
                    doc["n_points"] if status != "error" else 0,
                    doc["n_corners"] if status != "error" else 0,
                    doc["length_m"] if status != "error" else 0.0,
                    doc_bytes,
                    status,
                )
            )

    for track, doc_id, cfg, n_pts, n_cor, length_m, doc_bytes, status in rows:
        print(
            f"{track:<22}{doc_id:<34}{cfg:<22}"
            f"{n_pts:>8}{n_cor:>9}{length_m:>11.1f}{doc_bytes:>12}  {status}"
        )

    n_tracks = len(by_track)
    n_layouts = sum(len(v) for v in by_track.values())
    print()
    print(
        f"TOTALS: {n_tracks} tracks  {n_layouts} layouts  {total_points} points  "
        f"inserted={inserted} replaced={replaced} skipped={skipped} "
        f"errors={errors}  max_doc_bytes={max_doc_bytes}"
    )
    if used_heuristic_any:
        print(
            "WARNING: one or more trackConfiguration values came from the "
            "heuristic (multi-layout -> layout id, single-layout -> "
            "'track config'). "
            "Verify the printed (track, trackConfiguration) pairs against AC's "
            "real config strings; supply config_map.json to override."
        )

    if not args.dry_run:
        import mongo

        mongo.disconnect()

    return 1 if errors else 0


def main() -> None:
    sys.exit(run(parse_args()))


if __name__ == "__main__":
    main()
