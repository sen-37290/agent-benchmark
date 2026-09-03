#!/usr/bin/env python3
"""Collapse OpenHands per-event JSON files into one JSONL per session, reversibly.

A CyberGym run writes one tiny file per agent event:

    artifacts/cybergym/tasks/<task>/file/sessions/<uuid>/events/<id>.json

That is 96% of every file we ship. Hugging Face recommends staying under 100k files per repo and
*hard*-limits a single folder to 10k entries, so a few hundred-thousand-event run is both slow to
upload (throughput is files-per-commit, not bytes) and one chatty session away from rejection.

Each event file happens to be a single-line JSON object whose `id` equals its own filename, so the
directory carries no information that a JSONL cannot: sort by id, one object per line. This tool
does that collapse and can undo it byte-for-byte.

    ./repack_events.py pack   <root> [--jobs N] [--keep] [--dry-run]
    ./repack_events.py unpack <root> [--jobs N]
    ./repack_events.py verify <root> [--jobs N]

`pack` never deletes an `events/` directory until it has re-derived every original file from the
JSONL it just wrote and compared the bytes. A session that does not fit the contract (an event whose
`id` disagrees with its filename, a file spanning multiple lines, a stray non-`<id>.json` entry) is
reported and left completely untouched rather than guessed at.

The reversal data lives in `.events-repack.json` at the root, so `unpack` needs nothing but the
packed tree.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

MANIFEST_NAME = ".events-repack.json"
JSONL_NAME = "events.jsonl"
EVENTS_DIRNAME = "events"


class Skip(Exception):
    """A session that does not satisfy the lossless contract. Left on disk, untouched."""


# --------------------------------------------------------------------------- the contract


def digest(items: list[tuple[int, bytes]]) -> str:
    """A single hash over an events directory's exact contents, id order included."""
    h = hashlib.sha256()
    for event_id, raw in items:
        h.update(str(event_id).encode())
        h.update(b"\0")
        h.update(raw)
        h.update(b"\0")
    return h.hexdigest()


def read_events(events_dir: Path) -> tuple[list[tuple[int, bytes]], list[int]]:
    """Every event in id order, as exact bytes, plus the ids that ended with a newline.

    Raises Skip if the directory holds anything this tool cannot rebuild verbatim.
    """
    items: list[tuple[int, bytes]] = []
    newline_ids: list[int] = []
    for entry in sorted(os.listdir(events_dir)):
        path = events_dir / entry
        if not path.is_file():
            raise Skip(f"not a regular file: {entry}")
        stem, dot, ext = entry.rpartition(".")
        if ext != "json" or not dot or not stem.isdigit():
            raise Skip(f"unexpected entry: {entry}")
        event_id = int(stem)

        raw = path.read_bytes()
        body = raw
        if body.endswith(b"\n"):
            body = body[:-1]
            newline_ids.append(event_id)
        if b"\n" in body:
            raise Skip(f"{entry} spans multiple lines")

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise Skip(f"{entry} is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise Skip(f"{entry} is not a JSON object")
        # The id is what makes the filename redundant. Without it we cannot rebuild the name.
        if parsed.get("id") != event_id:
            raise Skip(f"{entry} carries id {parsed.get('id')!r}, not {event_id}")

        items.append((event_id, body))

    if not items:
        raise Skip("no events")
    items.sort(key=lambda pair: pair[0])
    return items, sorted(newline_ids)


def rebuild(lines: list[bytes], newline_ids: set[int]) -> list[tuple[int, bytes]]:
    """Turn JSONL lines back into (id, exact original bytes) pairs."""
    out: list[tuple[int, bytes]] = []
    for line in lines:
        event_id = json.loads(line)["id"]
        out.append((event_id, line + b"\n" if event_id in newline_ids else line))
    return out


# --------------------------------------------------------------------------- pack / unpack


def pack_session(root: str, rel_dir: str, keep: bool) -> dict:
    """Collapse one events/ directory. Verifies the round trip before removing anything."""
    events_dir = Path(root) / rel_dir
    try:
        items, newline_ids = read_events(events_dir)
    except Skip as exc:
        return {"dir": rel_dir, "skipped": str(exc)}

    original = digest(items)
    payload = b"\n".join(body for _, body in items) + b"\n"

    jsonl = events_dir.parent / JSONL_NAME
    tmp = jsonl.with_suffix(".jsonl.tmp")
    tmp.write_bytes(payload)

    # Prove reversibility against the bytes still on disk, then and only then delete them.
    lines = payload.rstrip(b"\n").split(b"\n")
    if digest(rebuild(lines, set(newline_ids))) != original:
        tmp.unlink(missing_ok=True)
        return {"dir": rel_dir, "skipped": "round trip did not reproduce the originals"}
    tmp.replace(jsonl)

    if not keep:
        shutil.rmtree(events_dir)

    return {
        "dir": rel_dir,
        "count": len(items),
        "ids": [event_id for event_id, _ in items],
        "nl_ids": newline_ids,
        "sha256": original,
        "jsonl_sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def unpack_session(root: str, entry: dict) -> dict:
    """Recreate one events/ directory from its JSONL, then check the bytes match the manifest."""
    events_dir = Path(root) / entry["dir"]
    jsonl = events_dir.parent / JSONL_NAME
    if not jsonl.exists():
        return {"dir": entry["dir"], "error": f"missing {JSONL_NAME}"}

    payload = jsonl.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry["jsonl_sha256"]:
        return {"dir": entry["dir"], "error": f"{JSONL_NAME} does not match the manifest"}

    items = rebuild(payload.rstrip(b"\n").split(b"\n"), set(entry["nl_ids"]))
    if digest([(i, b[:-1] if i in set(entry["nl_ids"]) else b) for i, b in items]) != entry["sha256"]:
        return {"dir": entry["dir"], "error": "rebuilt bytes do not match the manifest"}

    events_dir.mkdir(parents=True, exist_ok=True)
    for event_id, raw in items:
        (events_dir / f"{event_id}.json").write_bytes(raw)
    jsonl.unlink()
    return {"dir": entry["dir"], "count": len(items)}


def verify_session(root: str, entry: dict) -> dict:
    """Re-derive the originals in memory and compare to the manifest. Touches nothing."""
    jsonl = Path(root) / entry["dir"] / ".." / JSONL_NAME
    jsonl = jsonl.resolve()
    if not jsonl.exists():
        return {"dir": entry["dir"], "error": f"missing {JSONL_NAME}"}
    payload = jsonl.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry["jsonl_sha256"]:
        return {"dir": entry["dir"], "error": f"{JSONL_NAME} does not match the manifest"}
    newline_ids = set(entry["nl_ids"])
    items = rebuild(payload.rstrip(b"\n").split(b"\n"), newline_ids)
    stripped = [(i, b[:-1] if i in newline_ids else b) for i, b in items]
    if digest(stripped) != entry["sha256"]:
        return {"dir": entry["dir"], "error": "rebuilt bytes do not match the manifest"}
    if [i for i, _ in stripped] != entry["ids"]:
        return {"dir": entry["dir"], "error": "id list does not match the manifest"}
    return {"dir": entry["dir"], "count": entry["count"]}


# --------------------------------------------------------------------------- driving


def find_event_dirs(root: Path) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, _ in os.walk(root):
        if EVENTS_DIRNAME in dirnames:
            found.append(str((Path(dirpath) / EVENTS_DIRNAME).relative_to(root)))
    return sorted(found)


def run_parallel(fn, jobs: int, tasks: list) -> list[dict]:
    if jobs <= 1:
        return [fn(*t) for t in tasks]
    with futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(fn, *zip(*tasks))) if tasks else []


def cmd_pack(args) -> int:
    root = Path(args.root).resolve()
    dirs = find_event_dirs(root)
    total = sum(len(os.listdir(root / d)) for d in dirs)
    print(f"{len(dirs)} sessions, {total} event files under {root}")
    if args.dry_run:
        print(f"dry run: would leave {len(dirs)} events.jsonl in their place")
        return 0

    results = run_parallel(pack_session, args.jobs, [(str(root), d, args.keep) for d in dirs])
    packed = [r for r in results if "skipped" not in r]
    skipped = [r for r in results if "skipped" in r]

    manifest = {
        "version": 1,
        "root": str(root),
        "jsonl_name": JSONL_NAME,
        "sessions": packed,
        "skipped": skipped,
        "event_files": sum(r["count"] for r in packed),
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1) + "\n")

    print(f"packed  {len(packed)} sessions / {manifest['event_files']} events -> {len(packed)} files")
    if skipped:
        print(f"skipped {len(skipped)} sessions (left untouched):")
        for r in skipped[:20]:
            print(f"  {r['dir']}: {r['skipped']}")
    print(f"manifest: {root / MANIFEST_NAME}")
    return 1 if skipped else 0


def load_manifest(root: Path) -> dict:
    path = root / MANIFEST_NAME
    if not path.exists():
        sys.exit(f"no {MANIFEST_NAME} at {root}; nothing to reverse")
    return json.loads(path.read_text())


def cmd_unpack(args) -> int:
    root = Path(args.root).resolve()
    manifest = load_manifest(root)
    sessions = manifest["sessions"]
    results = run_parallel(unpack_session, args.jobs, [(str(root), e) for e in sessions])
    failed = [r for r in results if "error" in r]
    restored = sum(r.get("count", 0) for r in results if "error" not in r)
    print(f"restored {len(results) - len(failed)} sessions / {restored} event files")
    for r in failed[:20]:
        print(f"  FAIL {r['dir']}: {r['error']}")
    if not failed:
        (root / MANIFEST_NAME).unlink()
    return 1 if failed else 0


def cmd_verify(args) -> int:
    root = Path(args.root).resolve()
    manifest = load_manifest(root)
    results = run_parallel(verify_session, args.jobs, [(str(root), e) for e in manifest["sessions"]])
    failed = [r for r in results if "error" in r]
    ok = len(results) - len(failed)
    print(f"verified {ok}/{len(results)} sessions reproduce their originals byte-for-byte")
    for r in failed[:20]:
        print(f"  FAIL {r['dir']}: {r['error']}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, helptext in (
        ("pack", cmd_pack, "collapse every events/ directory into a sibling events.jsonl"),
        ("unpack", cmd_unpack, "restore every events/ directory from its events.jsonl"),
        ("verify", cmd_verify, "check a packed tree still reproduces the originals"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("root")
        p.add_argument("--jobs", type=int, default=min(32, (os.cpu_count() or 4)))
        if name == "pack":
            p.add_argument("--keep", action="store_true", help="write the JSONL but keep events/")
            p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func=fn)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
