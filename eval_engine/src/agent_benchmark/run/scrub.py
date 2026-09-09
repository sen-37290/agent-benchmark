"""Remove provider secrets from collected run artifacts.

``run_logged`` already redacts the engine's own subprocess stream, but that only covers what the
engine writes. Terminal-Bench artifacts are written by the agent *inside the container* --
``agent/trajectory.json``, ``agent/terminus_2.pane``, ``agent/recording.cast``, ``trial.log`` --
and never pass through that stream, so the guard was real but aimed at the wrong artifact. This
module is the second line: after a job completes, scrub its tree before anything is graded,
archived or uploaded.

Keeping the key out of the container (see ``agents/terminus_2.py``) is the actual fix; scrubbing
stops publication, not exposure. Both are kept: a future harness, a task that prints a key it
found elsewhere, or a provider that echoes one back would all land here.

Why a plain ``text.replace(secret, ...)`` is not enough
------------------------------------------------------
The pane recording is a fixed-width terminal capture, so a 108-character key gets broken across
lines and the artifact holds it with a literal ``\\n`` (or real whitespace) *inside* the value.
A literal replace silently misses those copies -- which is how one leak looked like a harmless
27-character fragment when the full key was in fact recoverable. Matching therefore has to
tolerate separators between any two characters of the secret.
"""

from __future__ import annotations

import re
from pathlib import Path

PLACEHOLDER = "<redacted>"

#: Separators that a wrapped terminal capture can insert *inside* a value: real whitespace, and
#: the escaped forms that survive JSON encoding (``\n``, ``\r\n``, and doubled backslashes).
_SEPARATOR = re.compile(r"(?:\\+[nrt]|\s)+")

#: Artifacts worth scanning. Recordings and trajectories are the ones that actually captured keys;
#: the rest are cheap and have carried fragments before.
TEXT_SUFFIXES = frozenset(
    {".json", ".jsonl", ".log", ".txt", ".cast", ".pane", ".yaml", ".yml", ".md", ".sh", ".env"}
)

#: Below this length a "secret" is too short to match safely without false positives.
MIN_SECRET_LENGTH = 12


def _normalise(text: str) -> tuple[str, list[int]]:
    """Return ``text`` with separators removed, plus each kept character's original offset."""
    kept: list[str] = []
    offsets: list[int] = []
    index = 0
    length = len(text)
    while index < length:
        match = _SEPARATOR.match(text, index)
        if match is not None and match.end() > index:
            index = match.end()
            continue
        kept.append(text[index])
        offsets.append(index)
        index += 1
    return "".join(kept), offsets


def secret_spans(text: str, secret: str) -> list[tuple[int, int]]:
    """Locate every copy of ``secret`` in ``text``, including copies broken by line wrapping.

    Linear in the size of the text: the separators are stripped once and the match is mapped back
    through the offset table, rather than backtracking a per-character regex over the whole file.
    """
    if len(secret) < MIN_SECRET_LENGTH:
        return []
    normalised, offsets = _normalise(text)
    target, _ = _normalise(secret)
    if not target:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        found = normalised.find(target, start)
        if found < 0:
            return spans
        spans.append((offsets[found], offsets[found + len(target) - 1] + 1))
        start = found + len(target)


def scrub_text(text: str, secrets: list[str], placeholder: str = PLACEHOLDER) -> tuple[str, int]:
    """Replace every copy of each secret. Returns the new text and the number of replacements."""
    spans: list[tuple[int, int]] = []
    for secret in secrets:
        if secret:
            spans.extend(secret_spans(text, secret))
    if not spans:
        return text, 0
    # Right to left, so earlier offsets stay valid. Overlaps cannot happen between distinct
    # secrets in practice, but drop any that do rather than corrupt the surrounding bytes.
    spans.sort(reverse=True)
    result = text
    replaced = 0
    previous_start = len(text) + 1
    for begin, end in spans:
        if end > previous_start:
            continue
        result = result[:begin] + placeholder + result[end:]
        previous_start = begin
        replaced += 1
    return result, replaced


def scrub_file(path: Path, secrets: list[str], placeholder: str = PLACEHOLDER) -> int:
    """Scrub one file in place. Returns the number of replacements made."""
    try:
        original = path.read_text(encoding="utf-8", errors="surrogateescape")
    except (OSError, UnicodeDecodeError):
        return 0
    cleaned, replaced = scrub_text(original, secrets, placeholder)
    if replaced:
        try:
            path.write_text(cleaned, encoding="utf-8", errors="surrogateescape")
        except OSError:
            return 0
    return replaced


def scrub_tree(
    root: Path, secrets: list[str], placeholder: str = PLACEHOLDER
) -> tuple[int, int, list[str]]:
    """Scrub every text artifact under ``root``.

    Returns ``(files_changed, replacements, relative_paths)``. Never raises: a run must not be
    lost because one artifact could not be rewritten, and the caller logs what was touched.
    """
    usable = [secret for secret in secrets if secret and len(secret) >= MIN_SECRET_LENGTH]
    if not usable or not root.exists():
        return 0, 0, []
    files_changed = 0
    replacements = 0
    touched: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        count = scrub_file(path, usable, placeholder)
        if count:
            files_changed += 1
            replacements += count
            touched.append(str(path.relative_to(root)))
    return files_changed, replacements, touched


def find_secrets(root: Path, secrets: list[str]) -> list[str]:
    """Relative paths of artifacts under ``root`` that still contain a secret. For tests/audits."""
    usable = [secret for secret in secrets if secret and len(secret) >= MIN_SECRET_LENGTH]
    if not usable or not root.exists():
        return []
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
        except (OSError, UnicodeDecodeError):
            continue
        if any(secret_spans(text, secret) for secret in usable):
            hits.append(str(path.relative_to(root)))
    return hits
