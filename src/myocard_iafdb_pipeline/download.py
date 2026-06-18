"""Download IAFDB records from PhysioNet, with SHA-256 integrity checks."""

from __future__ import annotations

import hashlib
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from tqdm import tqdm

from myocard_iafdb_pipeline.constants import (
    PHYSIONET_BASE_URL,
    RECORD_NAMES,
)

EXTENSIONS: tuple[str, ...] = (".dat", ".hea", ".qrs")
INDEX_FILES: tuple[str, ...] = ("RECORDS", "ANNOTATORS", "SHA256SUMS.txt")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_sha256sums(path: Path) -> dict[str, str]:
    """Parse a SHA256SUMS file (lines of ``<hash>  <filename>``)."""
    sums: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        sums[name.lstrip("*").strip()] = digest
    return sums


def _download_one(url: str, dest: Path, expected_hash: str | None = None) -> None:
    """Download a single file, skipping if already present with matching hash."""
    if dest.exists() and expected_hash is not None and _sha256(dest) == expected_hash:
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
            total = int(resp.headers.get("Content-Length", 0)) or None
            with tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=dest.name,
                leave=False,
            ) as bar:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    bar.update(len(chunk))
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise

    if expected_hash is not None:
        actual = _sha256(dest)
        if actual != expected_hash:
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"Hash mismatch for {dest.name}: expected {expected_hash}, got {actual}"
            )


def download(
    dest: Path | str,
    records: Iterable[str] | None = None,
) -> Path:
    """Download IAFDB records to ``dest``.

    Parameters
    ----------
    dest
        Destination directory. Created if it doesn't exist.
    records
        Record names to download (e.g. ``["iaf1_afw"]``). If ``None``, all
        32 records are downloaded.

    Returns
    -------
    Path
        Resolved destination directory.

    Raises
    ------
    ValueError
        If any element of ``records`` is not a known IAFDB record name.
    RuntimeError
        If a downloaded file fails the SHA-256 integrity check.
    """
    dest = Path(dest)
    record_list = list(records) if records is not None else list(RECORD_NAMES)
    unknown = sorted(set(record_list) - set(RECORD_NAMES))
    if unknown:
        raise ValueError(f"Unknown record names: {unknown}")

    dest.mkdir(parents=True, exist_ok=True)

    print(f"Fetching index files into {dest} ...")
    for name in INDEX_FILES:
        _download_one(f"{PHYSIONET_BASE_URL}/{name}", dest / name)

    expected = _parse_sha256sums(dest / "SHA256SUMS.txt")

    print(f"Downloading {len(record_list)} record(s) (~{len(record_list) * 3:d} files) ...")
    for record in tqdm(record_list, desc="records"):
        for ext in EXTENSIONS:
            filename = f"{record}{ext}"
            _download_one(
                f"{PHYSIONET_BASE_URL}/{filename}",
                dest / filename,
                expected_hash=expected.get(filename),
            )

    print(f"Done. Files saved to {dest}")
    return dest
