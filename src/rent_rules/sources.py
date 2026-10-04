"""Load source snapshots without changing their text, and split them losslessly."""

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SourceDocument:
    """A manifest entry and, when available, its exact local UTF-8 snapshot."""

    doc_id: str
    jurisdictions: str
    url: str
    source_type: str
    retrieved_at: str
    text: str
    path: Optional[Path]
    sha256: str
    capture_status: str


_MANIFEST_FIELDS = {
    "doc_id", "jurisdictions", "url", "source_type", "capture",
    "retrieved_at", "sha256", "text_file", "status",
}
_DOC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _safe_file(root: Path, relative: str, label: str) -> Path:
    """Reject both textual traversal and symlinks that leave the source root."""
    posix = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or "\x00" in relative
        or posix.is_absolute()
        or ".." in posix.parts
        or re.match(r"^[A-Za-z]:", relative)
    ):
        raise ValueError("{}: illegal relative path {!r}".format(label, relative))
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError) as exc:
        raise ValueError("{}: path escapes source directory: {!r}".format(label, relative)) from exc
    if not candidate.is_file():
        raise ValueError("{}: source file is missing or not a regular file: {}".format(label, candidate))
    return candidate


def _read_text(path: Path, label: str) -> Tuple[str, str]:
    try:
        raw = path.read_bytes()
        # Decoding bytes directly preserves CRLF, whitespace, and a possible BOM.
        return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise ValueError("{}: cannot read UTF-8 source file {}: {}".format(label, path, exc)) from exc


def _metadata(text: str, label: str) -> Tuple[Dict[str, str], int]:
    """Read leading capture headers; return the offset of the unmodified body."""
    metadata = {}  # type: Dict[str, str]
    offset = 0
    header_block_closed = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if offset == 0:
            stripped = stripped.lstrip("\ufeff")
        if not stripped:
            if metadata:
                header_block_closed = True
            offset += len(line)
            continue
        if header_block_closed:
            break
        key, separator, value = stripped.partition(":")
        key = key.strip()
        if not separator or key not in ("SOURCE", "RETRIEVED"):
            break
        if key in metadata:
            raise ValueError("{}: duplicate {} capture header".format(label, key))
        if not value.strip():
            raise ValueError("{}: empty {} capture header".format(label, key))
        metadata[key] = value.strip()
        offset += len(line)
    return metadata, offset


def _retrieval_time(value: str, label: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith(" UTC"):
        normalized = normalized[:-4] + "+00:00"
    elif normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone is required")
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("{}: invalid retrieval timestamp {!r}: {}".format(label, value, exc)) from exc


def _validate_url(value: str, label: str) -> None:
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        valid = False
    if not valid or any(character.isspace() for character in value):
        raise ValueError("{}: invalid source URL {!r}".format(label, value))


def load_sources(
    pack: Path, supplemental_dir: Optional[Path] = None
) -> Dict[str, SourceDocument]:
    """Load every manifest row, including entries without a local snapshot.

    A supplemental ``{doc_id}.txt`` overrides a supplied snapshot. Its SOURCE and
    RETRIEVED headers describe the new capture; an acquisition log should retain
    any redirects from the original manifest URL. Supplied snapshot headers must
    agree with the manifest. Manifest hashes are not used as local text hashes:
    they can describe the original downloaded resource rather than its text copy.
    """
    corpus = Path(pack) / "corpus"
    manifest = _safe_file(corpus, "corpus_manifest.csv", "manifest")
    supplement = None  # type: Optional[Path]
    if supplemental_dir is not None:
        supplement = Path(supplemental_dir).resolve()
        if not supplement.is_dir():
            raise ValueError("Supplemental source directory is missing or not a directory: {}".format(supplement))

    try:
        with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            fields = reader.fieldnames or []
            missing = _MANIFEST_FIELDS.difference(fields)
            if missing:
                raise ValueError("Manifest is missing columns: {}".format(", ".join(sorted(missing))))
            if len(fields) != len(set(fields)):
                raise ValueError("Manifest contains duplicate column names")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError("Cannot read source manifest {}: {}".format(manifest, exc)) from exc

    sources = {}  # type: Dict[str, SourceDocument]
    for row_number, row in enumerate(rows, start=2):
        if None in row or any(row.get(key) is None for key in _MANIFEST_FIELDS):
            raise ValueError("Manifest row {} has an invalid number of columns".format(row_number))
        doc_id = row["doc_id"].strip()
        if not _DOC_ID.fullmatch(doc_id):
            raise ValueError("Manifest row {} has an invalid doc_id {!r}".format(row_number, doc_id))
        if doc_id in sources:
            raise ValueError("Manifest contains duplicate doc_id {!r}".format(doc_id))
        url = row["url"].strip()
        _validate_url(url, doc_id)
        retrieved_at = row["retrieved_at"].strip()
        if retrieved_at:
            _retrieval_time(retrieved_at, doc_id + " manifest")
        capture = row["capture"].strip()
        capture_status = (
            capture if capture and capture != "yes"
            else row["status"].strip() or capture or "unavailable"
        )
        text, digest = "", ""
        path = None  # type: Optional[Path]
        relative = row["text_file"].strip()
        if relative:
            path = _safe_file(corpus, relative, doc_id)
            text, digest = _read_text(path, doc_id)
            headers, _ = _metadata(text, doc_id)
            missing_headers = {"SOURCE", "RETRIEVED"}.difference(headers)
            if missing_headers:
                raise ValueError("{}: missing capture headers: {}".format(doc_id, ", ".join(sorted(missing_headers))))
            if headers["SOURCE"] != url:
                raise ValueError("{}: SOURCE header conflicts with manifest URL".format(doc_id))
            header_time = _retrieval_time(headers["RETRIEVED"], doc_id + " header")
            if not retrieved_at or header_time != _retrieval_time(retrieved_at, doc_id + " manifest"):
                raise ValueError("{}: RETRIEVED header conflicts with manifest timestamp".format(doc_id))

        if supplement is not None:
            candidate = supplement / (doc_id + ".txt")
            # lexists-like handling also rejects dangling or escaping symlinks.
            if candidate.exists() or candidate.is_symlink():
                path = _safe_file(supplement, doc_id + ".txt", doc_id + " supplemental")
                text, digest = _read_text(path, doc_id + " supplemental")
                headers, _ = _metadata(text, doc_id + " supplemental")
                missing_headers = {"SOURCE", "RETRIEVED"}.difference(headers)
                if missing_headers:
                    raise ValueError("{}: supplemental source is missing capture headers: {}".format(doc_id, ", ".join(sorted(missing_headers))))
                url = headers["SOURCE"]
                _validate_url(url, doc_id + " supplemental")
                parsed = _retrieval_time(headers["RETRIEVED"], doc_id + " supplemental")
                retrieved_at = parsed.isoformat().replace("+00:00", "Z")
                capture_status = "supplemental"

        sources[doc_id] = SourceDocument(
            doc_id=doc_id,
            jurisdictions=row["jurisdictions"].strip(),
            url=url,
            source_type=row["source_type"].strip(),
            retrieved_at=retrieved_at,
            text=text,
            path=path,
            sha256=digest,
            capture_status=capture_status,
        )
    return sources


def source_inventory(sources: Dict[str, SourceDocument]) -> List[dict]:
    """Return serializable provenance, availability, and exact-body duplicates."""
    body_groups = {}  # type: Dict[str, List[str]]
    body_hashes = {}  # type: Dict[str, str]
    for doc_id, source in sources.items():
        if source.text:
            headers, offset = _metadata(source.text, doc_id)
            body = source.text[offset:] if headers else source.text
            if body.strip():
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                body_hashes[doc_id] = digest
                body_groups.setdefault(digest, []).append(doc_id)

    inventory = []  # type: List[dict]
    for doc_id in sorted(sources):
        source = sources[doc_id]
        body_hash = body_hashes.get(doc_id)
        duplicates = body_groups.get(body_hash, [])
        inventory.append({
            "doc_id": source.doc_id,
            "jurisdictions": source.jurisdictions,
            "url": source.url,
            "source_type": source.source_type,
            "retrieved_at": source.retrieved_at,
            "path": str(source.path) if source.path is not None else None,
            "capture_status": source.capture_status,
            "available": bool(body_hash),
            "char_count": len(source.text),
            "word_count": len(source.text.split()),
            "sha256": source.sha256,
            "body_sha256": body_hash,
            "duplicate_body_group": sorted(duplicates) if len(duplicates) > 1 else [],
        })
    return inventory


def chunk_document(
    source: SourceDocument, max_chars: int = 24000, overlap: int = 1200
) -> List[dict]:
    """Return overlapping exact slices covering the entire original snapshot.

    Offsets are zero-based Python character offsets, with an exclusive end.
    Capture headers are retained; no source characters are normalized or dropped.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    if isinstance(overlap, bool) or not isinstance(overlap, int) or not 0 <= overlap < max_chars:
        raise ValueError("overlap must be an integer from zero to max_chars - 1")
    chunks = []  # type: List[dict]
    start = 0
    while start < len(source.text):
        end = min(start + max_chars, len(source.text))
        if end < len(source.text):
            # Keep useful paragraph boundaries without creating non-progressing
            # chunks when the requested overlap is large.
            lower = start + max(max_chars // 2, overlap)
            newline = source.text.rfind("\n", lower, end)
            if newline >= lower:
                end = newline + 1
        chunks.append({
            "chunk_id": "{}:{:04d}".format(source.doc_id, len(chunks) + 1),
            "doc_id": source.doc_id,
            "start": start,
            "end": end,
            "text": source.text[start:end],
        })
        if end == len(source.text):
            break
        start = end - overlap
    return chunks
