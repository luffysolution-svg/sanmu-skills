"""Overwrite-safe persistence shared by API and browser acquisition lanes.

An acquisition directory is adopted only when its manifest and ``paper.pdf``
still agree on SHA-256, size, schema, and DOI. Obtained bytes are never
replaced by a later run; a conflicting rerun fails closed instead.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

SCHEMA = "sanmu-acquisition/v1"


class AcquisitionConflict(RuntimeError):
    """Existing acquisition state disagrees with the new download."""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def inspect_existing(dest_dir, doi=None):
    """Return verified existing state, ``None`` for an unused directory.

    Raises AcquisitionConflict when a directory contains anything that cannot
    be proven to be the same obtained PDF. The caller must not overwrite it.
    """
    dest = Path(dest_dir)
    if not dest.exists():
        return None
    if not dest.is_dir():
        raise AcquisitionConflict("Acquisition path exists but is not a directory")
    pdf = dest / "paper.pdf"
    manifest_path = dest / "manifest.json"
    if not manifest_path.exists() and not pdf.exists():
        leftovers = sorted(item.name for item in dest.iterdir())
        if leftovers:
            raise AcquisitionConflict(
                "Acquisition directory has unexpected files: " + ", ".join(leftovers[:5])
            )
        return None
    if not (manifest_path.is_file() and pdf.is_file()):
        raise AcquisitionConflict(
            "Acquisition directory contains an unmanaged paper.pdf or manifest.json"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AcquisitionConflict("Existing acquisition manifest is unreadable") from exc
    if manifest.get("schema") != SCHEMA:
        raise AcquisitionConflict("Existing acquisition manifest has an unsupported schema")
    main = manifest.get("main") or {}
    if main.get("file") != "paper.pdf":
        raise AcquisitionConflict("Existing manifest does not describe paper.pdf")
    expected = str(main.get("sha256") or "").lower()
    actual = sha256_file(pdf)
    if not expected or actual != expected:
        raise AcquisitionConflict("Existing paper.pdf no longer matches its manifest SHA-256")
    existing_doi = str((manifest.get("paper") or {}).get("doi") or "").strip().lower()
    if doi and existing_doi and existing_doi != str(doi).strip().lower():
        raise AcquisitionConflict("Existing acquisition belongs to a different DOI")
    return {
        "status": "already_obtained",
        "file": str(pdf),
        "manifest": str(manifest_path),
        "sha256": actual,
        "bytes": pdf.stat().st_size,
        "doi": existing_doi or None,
        "identity": main.get("identity") or {},
    }


def persist(dest_dir, data, manifest):
    """Write a new PDF and manifest without ever replacing different bytes."""
    dest = Path(dest_dir)
    doi = str((manifest.get("paper") or {}).get("doi") or "").strip() or None
    existing = inspect_existing(dest, doi)
    if existing is not None:
        expected = str((manifest.get("main") or {}).get("sha256") or "").lower()
        if expected and existing["sha256"] != expected:
            raise AcquisitionConflict(
                "Existing paper.pdf differs from the newly downloaded file; refusing overwrite"
            )
        return existing
    dest.mkdir(parents=True, exist_ok=True)
    payload = manifest.get("main") or {}
    expected = str(payload.get("sha256") or "").lower()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise AcquisitionConflict("Manifest SHA-256 does not match the downloaded bytes")
    _atomic_write(dest / "paper.pdf", data)
    _atomic_write(
        dest / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return {
        "status": "file_obtained",
        "file": str(dest / "paper.pdf"),
        "manifest": str(dest / "manifest.json"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def ingest_status(result):
    """Map an ingest.py result to the acquisition record's own status."""
    status = (result or {}).get("status")
    if status == "success":
        return "ingested"
    if status == "partial":
        return "ingested_partial"
    return "file_obtained"


def write_json(path, value):
    _atomic_write(Path(path),
                  (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def is_success(record):
    """Whether a batch item represents a complete, truthful outcome."""
    if (record or {}).get("ingest_error"):
        return False
    ingest = (record or {}).get("ingest") or {}
    if ingest.get("status") == "pending_identity_review":
        return False
    status = (record or {}).get("status")
    if status in ("file_obtained", "ingested"):
        return True
    return status == "skipped" and record.get("reason") in (
        "main_pdf_exists",
        "already_obtained",
    )
