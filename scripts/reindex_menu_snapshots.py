"""Rebuild the local snapshot index without contacting the menu API."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def snapshot_index_entry(record: object, filename: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError("snapshot must be an object")
    if not all(
        isinstance(record.get(key), str)
        for key in ("brand_name", "store_name", "store_id")
    ) or not record["store_id"].strip():
        raise ValueError("missing or invalid snapshot metadata")
    raw = record.get("raw_menu")
    if not isinstance(raw, dict) or raw.get("status", 200) != 200:
        raise ValueError("snapshot does not contain a successful menu")
    menu = raw.get("menu")
    products = menu.get("product_list_json") if isinstance(menu, dict) else None
    data = products.get("data") if isinstance(products, dict) else None
    if not isinstance(data, (dict, list)):
        raise ValueError("missing or invalid product data")
    return {
        "brand": record["brand_name"],
        "store": record["store_name"],
        "store_id": record["store_id"],
        "file": filename,
        "product_count": len(data),
    }


def rebuild_index(directory: Path) -> list[dict]:
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("snapshot directory does not exist")
    entries = []
    # Validate the entire set before touching the previous successful index.
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            entries.append(snapshot_index_entry(record, path.name))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"Invalid snapshot {path.name} ({type(exc).__name__})"
            ) from exc

    fd, temporary = tempfile.mkstemp(
        prefix=".index-", suffix=".tmp", dir=directory
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entries, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, directory / "index.json")
    finally:
        temporary_path.unlink(missing_ok=True)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path,
                        default=Path("data/menus"))
    args = parser.parse_args(argv)
    try:
        entries = rebuild_index(args.directory)
    except (OSError, ValueError) as exc:
        print(f"Index rebuild failed: {exc}", file=sys.stderr)
        return 1
    print(f"Snapshots: {len(entries)}; index entries: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
