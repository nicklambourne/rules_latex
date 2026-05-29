#!/usr/bin/env python3
"""PDF chunking for incremental live-preview transfer.

This module is consumed by ``latex_live``'s server. After each
successful compile, the server runs ``compute_manifest`` on the
output PDF to produce a content-addressed view of the document:
each indirect PDF object becomes a chunk identified by the SHA-256
of its bytes. The server stores chunks on disk; the browser fetches
only the chunks whose hashes it does not already have, then PDF.js
stitches the document back together for rendering.

Why this matters
----------------

Without chunking, every save → recompile → reload cycle re-downloads
the entire PDF. For a one-page CV that's ~25 KB and barely
noticeable. For a 50-page thesis it's several megabytes per edit —
the bulk of which is unchanged font subsets and unchanged page
content streams. With chunking, a body edit on page 30 produces
new bytes for one page object (~10 KB), the xref stream (~1 KB),
and the trailer (~20 B) — everything else is a client-cache hit.

PDF format
----------

Tectonic (via xdvipdfmx) emits modern PDFs with a **cross-reference
stream** in place of the classic ASCII xref table. We don't try to
handle every flavor of xref, just the two that appear in real
tectonic output:

* Cross-reference stream (PDF 1.5+, the common case). The xref data
  lives inside an indirect object with ``/Type /XRef``, compressed
  with FlateDecode, with a ``/W [w1 w2 w3]`` field telling us how
  many bytes per field of each xref entry. Entry types:

    * type 0 — free object (not in PDF, ignored).
    * type 1 — uncompressed: present at ``offset`` bytes from PDF
               start. THIS is what we content-address.
    * type 2 — compressed: lives inside another object stream.
               We don't chunk these individually because their
               bytes aren't directly addressable; instead they
               travel as part of the containing object stream
               (which is itself a type-1 object).

* Classic xref table (older PDFs, rare from tectonic). Begins with
  the keyword ``xref`` followed by subsections of fixed-width
  ASCII entries. We parse this fallback for completeness.

The parser is intentionally minimal: enough to enumerate the
uncompressed objects and find their byte ranges. We don't decode
the PDF document structure, follow references, or care about
content. If anything looks weird, the caller falls back to
whole-PDF transport — the user just doesn't get the chunking
optimization for that document.

Hashing
-------

Each uncompressed object's chunk is its raw byte range from PDF
start to (next uncompressed object's offset, or `startxref` if it
is the last). We do NOT hash post-object whitespace separately:
between-object whitespace is rare in tectonic output and bundling
it with the preceding object keeps the chunk count manageable.

Determinism: the chunk store is content-addressed (SHA-256), so
two identical PDFs always produce the same chunk set regardless
of file modification times or paths.

Limits
------

* Caller must supply ``pdf_size`` (avoids a stat round-trip and
  protects against multi-GB files that we wouldn't want to chunk
  anyway — limit is enforced at MAX_PDF_SIZE).
* Cross-reference stream's compressed payload is capped at
  MAX_XREF_STREAM_SIZE to bound decompression cost.

Returns
-------

``compute_manifest`` returns either a ``Manifest`` (success) or
``None`` (any parse error -- caller falls back to whole-PDF).
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import struct
import zlib
from pathlib import Path
from typing import Optional


# Reject PDFs larger than 256 MB. The chunking optimization is
# pointless past a certain size (the user has bigger problems) and
# the parser allocates a few buffers proportional to file size.
MAX_PDF_SIZE = 256 * 1024 * 1024

# Cap the compressed-xref-stream size we'll decompress. The
# uncompressed result is roughly 5x larger; 4 MiB compressed means
# up to ~20 MiB decompressed, which is enough for ~4 million xref
# entries — far more than any sane document.
MAX_XREF_STREAM_SIZE = 4 * 1024 * 1024

# Largest tail we'll scan for the startxref keyword. PDF spec says
# the trailer is at the file end and shorter than this in practice
# (usually <100 bytes).
_TAIL_SCAN_SIZE = 1024


@dataclasses.dataclass(frozen=True)
class Chunk:
    """One content-addressed PDF chunk.

    ``object_id`` is the PDF object number ("21 0 obj" -> 21). It is
    informational only; chunks are identified by ``hash``.

    ``start`` and ``end`` are byte offsets into the original PDF
    (half-open: ``[start, end)``). They are not stored on the
    server side except as keys into the layout — but the client
    needs them to slot the chunk back into PDF.js's view of the
    file.

    ``hash`` is the lowercase hex SHA-256 of the bytes
    ``pdf[start:end]``. The server stores chunks under
    ``<chunks_dir>/<hash>``.
    """

    object_id: int
    start: int
    end: int
    hash: str


@dataclasses.dataclass(frozen=True)
class PageInfo:
    """Per-page identity for incremental render reuse (DESIGN.md §5 #13,
    option B).

    ``content_hash`` is the SHA-256 (hex) of the page's content-stream
    object(s) — derived from the same content-addressed chunk hashes, so
    a body edit that rewrites one page's content stream changes only that
    page's ``content_hash``. ``width`` / ``height`` are the MediaBox
    dimensions in PDF points (possibly inherited from an ancestor
    ``/Pages`` node). The browser diffs ``content_hash`` across reloads to
    skip re-rendering pages whose content is unchanged.

    Best-effort: ``Manifest.pages`` is empty when the page tree can't be
    resolved (e.g. an unsupported structure), and the client simply
    falls back to re-rendering every visible page.
    """

    content_hash: str
    width: float
    height: float


@dataclasses.dataclass(frozen=True)
class Manifest:
    """A complete chunk manifest for one PDF.

    ``pdf_size`` is the total file size. The client constructs a
    ``PDFDataRangeTransport`` with ``length = pdf_size`` so PDF.js
    knows the total document size up front.

    ``chunks`` lists every uncompressed PDF object in offset
    order. ``skeleton_ranges`` is the complement: byte ranges
    *not* covered by any chunk (PDF header, gaps between objects,
    the trailer line). The client fetches those from ``/pdf``
    directly using HTTP Range.

    Together the chunks + skeleton ranges cover the full byte
    range ``[0, pdf_size)`` without overlaps or gaps. Tested
    invariant in ``test_pdf_chunks``.

    ``pages`` is the per-page identity index (one ``PageInfo`` per page,
    in document order) used for incremental render reuse, or empty when
    the page tree couldn't be resolved.
    """

    pdf_size: int
    chunks: tuple
    # Pairs of (start, end) for byte ranges not covered by any chunk.
    skeleton_ranges: tuple
    pages: tuple = ()


def compute_manifest(
    pdf_path: Path,
    chunks_dir: Path,
) -> Optional[Manifest]:
    """Parse ``pdf_path``, write each chunk into ``chunks_dir``,
    return the manifest. Returns ``None`` on any parse failure so
    the caller can fall back to whole-PDF transport.

    Atomicity: chunk files are written via tmp-then-rename so a
    concurrent reader of ``chunks_dir`` never sees a partial
    chunk. Existing chunk files with the right hash are not
    rewritten (the hash-named file IS its own integrity check).
    """
    try:
        pdf_size = pdf_path.stat().st_size
    except OSError:
        return None
    if pdf_size <= 0 or pdf_size > MAX_PDF_SIZE:
        return None

    try:
        with open(pdf_path, "rb") as fp:
            data = fp.read()
    except OSError:
        return None

    try:
        offsets = _extract_uncompressed_offsets(data)
    except _ParseError:
        return None

    if not offsets:
        return None

    # Compute (start, end) for each object by sorting on offset and
    # using the next offset as the end. The last object's end is
    # the position of the trailing "startxref" keyword.
    try:
        startxref_pos = _find_startxref(data)
    except _ParseError:
        return None

    sorted_offsets = sorted(offsets, key=lambda x: x[1])
    chunks_list: list[Chunk] = []
    for i, (object_id, start) in enumerate(sorted_offsets):
        if i + 1 < len(sorted_offsets):
            end = sorted_offsets[i + 1][1]
        else:
            end = startxref_pos
        if end <= start:
            # Pathological PDF — bail.
            return None
        chunk_bytes = data[start:end]
        h = hashlib.sha256(chunk_bytes).hexdigest()
        chunks_list.append(Chunk(
            object_id=object_id,
            start=start,
            end=end,
            hash=h,
        ))

    # Write chunks to disk. We write each only if the destination
    # file doesn't already exist with the correct size. (Hash
    # collisions on the file content are effectively impossible
    # given SHA-256; the size check is a cheap freshness gate.)
    try:
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for chunk in chunks_list:
            dest = chunks_dir / chunk.hash
            if dest.is_file() and dest.stat().st_size == (chunk.end - chunk.start):
                continue
            _atomic_write_bytes(dest, data[chunk.start:chunk.end])
    except OSError:
        return None

    # Compute skeleton ranges: the byte ranges NOT covered by any
    # chunk. These are fetched directly from /pdf via HTTP Range.
    skeleton: list[tuple[int, int]] = []
    cursor = 0
    for chunk in chunks_list:
        if chunk.start > cursor:
            skeleton.append((cursor, chunk.start))
        cursor = chunk.end
    if cursor < pdf_size:
        skeleton.append((cursor, pdf_size))

    # Per-page content identity for incremental render reuse (option B,
    # DESIGN.md §5 #13). Best-effort: any parse failure yields an empty
    # page index, and the client falls back to re-rendering visible pages.
    # Reuses the chunk hashes already computed, so a page's hash changes
    # iff one of its content-stream objects changed.
    chunk_hash_by_objid = {c.object_id: c.hash for c in chunks_list}
    try:
        pages = _compute_page_index(data, chunk_hash_by_objid)
    except _ParseError:
        pages = ()

    return Manifest(
        pdf_size=pdf_size,
        chunks=tuple(chunks_list),
        skeleton_ranges=tuple(skeleton),
        pages=pages,
    )


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` via a same-directory tmpfile +
    atomic rename. Mirrors the pattern used by ``serve_cache.py``."""
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with open(tmp, "wb") as fp:
            fp.write(data)
        # POSIX rename is atomic within a directory.
        tmp.replace(dest)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# -----------------------------------------------------------------------------
# Parse internals
# -----------------------------------------------------------------------------


class _ParseError(Exception):
    """Internal: any failure that triggers a manifest-None fallback."""


def _find_startxref(data: bytes) -> int:
    """Return the byte offset of the trailing ``startxref`` keyword.

    Scans the last ``_TAIL_SCAN_SIZE`` bytes for the keyword. PDF
    spec requires ``startxref`` to be near the end of the file
    followed by an integer (the xref offset) and ``%%EOF``.
    """
    tail_start = max(0, len(data) - _TAIL_SCAN_SIZE)
    tail = data[tail_start:]
    idx = tail.rfind(b"startxref")
    if idx < 0:
        raise _ParseError("startxref not found near EOF")
    return tail_start + idx


def _extract_uncompressed_offsets(data: bytes) -> list[tuple[int, int]]:
    """Return a list of ``(object_id, byte_offset)`` for every
    uncompressed (type=1) object in the PDF.

    Tries the cross-reference stream path first (modern tectonic
    output); falls back to the classic xref table if no xref
    stream is found.
    """
    startxref_pos = _find_startxref(data)
    # After "startxref" comes an ASCII integer giving the offset
    # of the xref start.
    after = data[startxref_pos + len(b"startxref"):]
    m = re.match(rb"\s*(\d+)\s*\n", after)
    if not m:
        raise _ParseError("startxref offset not parseable")
    xref_offset = int(m.group(1))
    if xref_offset >= len(data):
        raise _ParseError("xref offset past EOF")

    # At ``xref_offset`` we have either:
    #   * the keyword "xref" (classic table), or
    #   * an object header like "N G obj << ... /Type /XRef ... >>" (stream).
    head = data[xref_offset:xref_offset + 64]
    if head.startswith(b"xref"):
        return _parse_classic_xref(data, xref_offset)
    return _parse_xref_stream(data, xref_offset)


def _parse_xref_stream(data: bytes, obj_offset: int) -> list[tuple[int, int]]:
    """Parse a PDF 1.5 cross-reference stream at ``obj_offset``.

    The object header is plain ASCII; we extract /W, /Size, and
    locate the compressed stream payload, decompress it, and split
    into entries.
    """
    # Find "obj" keyword (object header start).
    obj_kw = data.find(b"obj", obj_offset)
    if obj_kw < 0 or obj_kw - obj_offset > 32:
        raise _ParseError("xref stream object header malformed")

    # Find the dictionary boundaries: "<<...>>".
    dict_start = data.find(b"<<", obj_kw)
    dict_end = data.find(b">>", dict_start)
    if dict_start < 0 or dict_end < 0:
        raise _ParseError("xref stream dictionary not found")
    header_text = data[dict_start:dict_end].decode(
        "latin-1", errors="replace",
    )

    # Parse /W, /Size, /Filter from the header.
    w_match = re.search(
        r"/W\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s*\]",
        header_text,
    )
    if not w_match:
        raise _ParseError("xref stream /W missing")
    W = tuple(int(x) for x in w_match.groups())
    if sum(W) == 0:
        raise _ParseError("xref stream /W all zero")

    size_match = re.search(r"/Size\s+(\d+)", header_text)
    if not size_match:
        raise _ParseError("xref stream /Size missing")
    N = int(size_match.group(1))
    if N <= 0 or N > 10_000_000:
        raise _ParseError("xref stream /Size out of range")

    # Locate the stream payload. After the dictionary "...>>"
    # comes "\nstream\n" (or "\r\nstream\n"), then the bytes,
    # then "\nendstream".
    stream_kw = data.find(b"stream", dict_end)
    if stream_kw < 0:
        raise _ParseError("xref stream payload start not found")
    # The actual bytes start after "stream" + one CR/LF or LF.
    payload_start = stream_kw + len(b"stream")
    if payload_start < len(data) and data[payload_start:payload_start + 2] == b"\r\n":
        payload_start += 2
    elif payload_start < len(data) and data[payload_start:payload_start + 1] == b"\n":
        payload_start += 1
    else:
        raise _ParseError("xref stream payload prefix malformed")

    endstream_kw = data.find(b"endstream", payload_start)
    if endstream_kw < 0:
        raise _ParseError("endstream keyword not found")
    # Trim the LF/CRLF that PDF spec places before endstream.
    payload_end = endstream_kw
    if payload_end > payload_start and data[payload_end - 2:payload_end] == b"\r\n":
        payload_end -= 2
    elif payload_end > payload_start and data[payload_end - 1:payload_end] == b"\n":
        payload_end -= 1

    payload = data[payload_start:payload_end]
    if len(payload) > MAX_XREF_STREAM_SIZE:
        raise _ParseError("xref stream payload too large")

    # /Filter handling: we only support /FlateDecode (and no filter).
    filter_match = re.search(r"/Filter\s*(/\w+|\[\s*/\w+(?:\s+/\w+)*\s*\])",
                             header_text)
    if filter_match:
        filter_text = filter_match.group(1)
        if "FlateDecode" not in filter_text:
            raise _ParseError(f"unsupported xref stream filter: {filter_text}")
        try:
            decompressed = zlib.decompress(payload)
        except zlib.error as e:
            raise _ParseError(f"xref stream decompress failed: {e}")
    else:
        decompressed = payload

    # /Index lets the stream cover a non-contiguous range of object
    # numbers as [first count first count ...]. Default is [0 N].
    index_match = re.search(r"/Index\s*\[\s*([\d\s]+)\]", header_text)
    if index_match:
        nums = [int(x) for x in index_match.group(1).split()]
        if len(nums) < 2 or len(nums) % 2 != 0:
            raise _ParseError("xref stream /Index malformed")
        sections = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    else:
        sections = [(0, N)]

    entry_len = sum(W)
    expected = sum(count for _, count in sections) * entry_len
    if len(decompressed) < expected:
        raise _ParseError(
            f"xref stream decompressed payload too short: "
            f"{len(decompressed)} bytes, expected {expected}"
        )

    # Parse entries section by section, accumulating type=1 entries.
    uncompressed: list[tuple[int, int]] = []
    pos = 0
    for first_obj, count in sections:
        for i in range(count):
            object_id = first_obj + i
            fields = []
            for w in W:
                val = 0
                for _ in range(w):
                    val = (val << 8) | decompressed[pos]
                    pos += 1
                fields.append(val)
            type_ = fields[0]
            if type_ == 1:
                offset = fields[1]
                # gen = fields[2]
                if offset > 0:
                    uncompressed.append((object_id, offset))
    return uncompressed


def _parse_classic_xref(data: bytes, xref_offset: int) -> list[tuple[int, int]]:
    """Parse a classic ASCII xref table.

    Fallback path: tectonic doesn't usually emit these, but a
    user-supplied PDF might. The format:

        xref
        FIRSTOBJ COUNT
        offset(10) generation(5) n_or_f(1) eol(2)
        ...
        trailer ...
    """
    # Cursor scans line-by-line from after "xref\n".
    cursor = xref_offset + len(b"xref")
    if cursor < len(data) and data[cursor:cursor + 2] == b"\r\n":
        cursor += 2
    elif cursor < len(data) and data[cursor:cursor + 1] in (b"\n", b"\r"):
        cursor += 1
    uncompressed: list[tuple[int, int]] = []

    # Iterate subsections.
    while cursor < len(data):
        # Detect "trailer" keyword (end of xref data).
        if data[cursor:cursor + 7] == b"trailer":
            break
        # Subsection header: "FIRSTOBJ COUNT\n"
        nl = data.find(b"\n", cursor)
        if nl < 0:
            raise _ParseError("classic xref subsection header EOL not found")
        header_line = data[cursor:nl].strip()
        m = re.match(rb"(\d+)\s+(\d+)$", header_line)
        if not m:
            raise _ParseError(
                f"classic xref subsection header malformed: {header_line!r}"
            )
        first_obj = int(m.group(1))
        count = int(m.group(2))
        cursor = nl + 1
        # Each entry is exactly 20 bytes: 10-digit offset, space,
        # 5-digit gen, space, 1 letter (n or f), 2-byte EOL.
        for i in range(count):
            entry = data[cursor:cursor + 20]
            if len(entry) < 20:
                raise _ParseError("classic xref entry truncated")
            if entry[17:18] == b"n":
                try:
                    offset = int(entry[:10])
                except ValueError:
                    raise _ParseError("classic xref entry offset bad")
                if offset > 0:
                    uncompressed.append((first_obj + i, offset))
            cursor += 20

    return uncompressed


# -----------------------------------------------------------------------------
# Page index (option B): resolve the page tree to a per-page content hash.
#
# Walk /Root -> /Pages -> /Kids -> /Page, reading each dict from wherever
# it lives: a directly-addressable (type-1) object, or a compressed object
# stream (/ObjStm, type-2) — tectonic puts the whole page tree in an
# /ObjStm. For each leaf page, combine the chunk hashes of its /Contents
# object(s) into one content hash and read its (possibly inherited)
# /MediaBox geometry. Intentionally defensive: any surprise raises
# _ParseError and the caller drops the page index (the client then just
# re-renders every visible page — correctness is unaffected).
# -----------------------------------------------------------------------------

_MAX_PAGE_TREE_DEPTH = 50
_MAX_PAGES = 100000


def _compute_page_index(data, chunk_hash_by_objid):
    """Return a tuple of PageInfo in document order, or () when there's no
    resolvable /Root (e.g. the synthetic test PDFs and classic PDFs that
    omit it)."""
    locations, root_ref = _parse_object_locations(data)
    if root_ref is None:
        return ()
    objstm_cache = {}

    def resolve(obj_id):
        return _resolve_object_dict(data, obj_id, locations, objstm_cache)

    catalog = resolve(root_ref)
    if catalog is None:
        raise _ParseError("catalog not resolvable")
    pages_ref = _dict_ref(catalog, "Pages")
    if pages_ref is None:
        raise _ParseError("catalog has no /Pages")

    pages = []

    def walk(node_id, inherited_box, depth, seen):
        if depth > _MAX_PAGE_TREE_DEPTH:
            raise _ParseError("page tree too deep")
        if node_id in seen:
            raise _ParseError("page tree cycle")
        seen = seen | {node_id}
        text = resolve(node_id)
        if text is None:
            raise _ParseError("page-tree node %d not resolvable" % node_id)
        box = _dict_num_array(text, "MediaBox") or inherited_box
        typ = _dict_name(text, "Type")
        is_branch = typ == "Pages" or (
            typ != "Page" and re.search(r"/Kids\b", text) is not None
        )
        if is_branch:
            kids = _dict_ref_array(text, "Kids")
            if not kids:
                raise _ParseError("page-tree branch with no /Kids")
            for kid in kids:
                walk(kid, box, depth + 1, seen)
            return
        # Leaf /Page.
        if len(pages) >= _MAX_PAGES:
            raise _ParseError("too many pages")
        hashes = []
        for cid in _dict_ref_or_array(text, "Contents"):
            h = chunk_hash_by_objid.get(cid)
            if h is None:
                # Content stream isn't a directly-addressable chunk
                # (e.g. compressed in an objstm). Without it we can't give
                # a reliable per-page signal, so drop the whole index.
                raise _ParseError("page content obj %d not a chunk" % cid)
            hashes.append(h)
        if box is None:
            raise _ParseError("page has no MediaBox")
        content_hash = hashlib.sha256("".join(hashes).encode("ascii")).hexdigest()
        width = abs(box[2] - box[0])
        height = abs(box[3] - box[1])
        pages.append(PageInfo(content_hash=content_hash, width=width, height=height))

    walk(pages_ref, None, 0, frozenset())
    return tuple(pages)


def _parse_object_locations(data):
    """Map every in-use object to where its bytes live, plus the /Root id.

    Returns ``({obj_id: ("u", offset) | ("c", objstm_id, index)}, root_id)``
    where "u" is an uncompressed (type-1) object at ``offset`` and "c" is a
    compressed (type-2) object at ``index`` within object stream
    ``objstm_id``. ``root_id`` is None when the trailer has no /Root.
    """
    xref_offset = _xref_offset(data)
    head = data[xref_offset:xref_offset + 64]
    if head.startswith(b"xref"):
        return _locations_from_classic(data, xref_offset)
    return _locations_from_xref_stream(data, xref_offset)


def _xref_offset(data):
    startxref_pos = _find_startxref(data)
    m = re.match(rb"\s*(\d+)", data[startxref_pos + len(b"startxref"):])
    if not m:
        raise _ParseError("startxref offset not parseable")
    off = int(m.group(1))
    if off >= len(data):
        raise _ParseError("xref offset past EOF")
    return off


def _locations_from_xref_stream(data, obj_offset):
    dict_text = _extract_balanced_dict(data, obj_offset)
    if dict_text is None:
        raise _ParseError("xref stream dict not found")
    w_match = re.search(r"/W\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s*\]", dict_text)
    if not w_match:
        raise _ParseError("xref stream /W missing")
    W = tuple(int(x) for x in w_match.groups())
    if sum(W) == 0:
        raise _ParseError("xref stream /W all zero")
    size_match = re.search(r"/Size\s+(\d+)", dict_text)
    if not size_match:
        raise _ParseError("xref stream /Size missing")
    N = int(size_match.group(1))
    if N <= 0 or N > 10_000_000:
        raise _ParseError("xref stream /Size out of range")

    decompressed = _decode_stream(data, obj_offset, dict_text)

    index_match = re.search(r"/Index\s*\[\s*([\d\s]+)\]", dict_text)
    if index_match:
        nums = [int(x) for x in index_match.group(1).split()]
        if len(nums) < 2 or len(nums) % 2 != 0:
            raise _ParseError("xref stream /Index malformed")
        sections = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    else:
        sections = [(0, N)]

    entry_len = sum(W)
    if len(decompressed) < sum(c for _, c in sections) * entry_len:
        raise _ParseError("xref stream payload too short")

    locations = {}
    pos = 0
    for first_obj, count in sections:
        for i in range(count):
            object_id = first_obj + i
            fields = []
            for w in W:
                val = 0
                for _ in range(w):
                    val = (val << 8) | decompressed[pos]
                    pos += 1
                fields.append(val)
            # Default for a missing first field (W[0]==0) is type 1.
            type_ = fields[0] if W[0] != 0 else 1
            if type_ == 1 and fields[1] > 0:
                locations[object_id] = ("u", fields[1])
            elif type_ == 2:
                locations[object_id] = ("c", fields[1], fields[2])

    root_ref = _dict_ref(dict_text, "Root")
    return locations, root_ref


def _locations_from_classic(data, xref_offset):
    locations = {}
    for object_id, offset in _parse_classic_xref(data, xref_offset):
        locations[object_id] = ("u", offset)
    # /Root lives in the trailer dict after the "trailer" keyword.
    root_ref = None
    tpos = data.find(b"trailer", xref_offset)
    if tpos >= 0:
        trailer = _extract_balanced_dict(data, tpos)
        if trailer is not None:
            root_ref = _dict_ref(trailer, "Root")
    return locations, root_ref


def _resolve_object_dict(data, obj_id, locations, objstm_cache):
    """Return the latin-1 dict text ``<<...>>`` of an object, or None."""
    loc = locations.get(obj_id)
    if loc is None:
        return None
    if loc[0] == "u":
        objkw = data.find(b"obj", loc[1])
        if objkw < 0 or objkw - loc[1] > 40:
            return None
        return _extract_balanced_dict(data, objkw + 3)
    _, objstm_id, index = loc
    objs = objstm_cache.get(objstm_id)
    if objs is None:
        objs = _parse_objstm(data, objstm_id, locations)
        objstm_cache[objstm_id] = objs
    return objs.get(index)


def _parse_objstm(data, objstm_id, locations):
    """Decompress an object stream and return ``{index: dict_text}`` for the
    objects it contains (index = 0-based ordinal, matching type-2 entries)."""
    loc = locations.get(objstm_id)
    if loc is None or loc[0] != "u":
        raise _ParseError("objstm %d not an uncompressed object" % objstm_id)
    offset = loc[1]
    dict_text = _extract_balanced_dict(data, offset)
    if dict_text is None:
        raise _ParseError("objstm dict not found")
    n = _dict_int(dict_text, "N")
    first = _dict_int(dict_text, "First")
    if n is None or first is None:
        raise _ParseError("objstm missing /N or /First")
    if n < 0 or n > 1_000_000:
        raise _ParseError("objstm /N out of range")
    decompressed = _decode_stream(data, offset, dict_text)
    header = decompressed[:first].split()
    if len(header) < 2 * n:
        raise _ParseError("objstm header too short")
    rel_offsets = [int(header[2 * k + 1]) for k in range(n)]
    objs = {}
    for k in range(n):
        start = first + rel_offsets[k]
        end = first + rel_offsets[k + 1] if k + 1 < n else len(decompressed)
        if start > end or end > len(decompressed):
            raise _ParseError("objstm offset out of range")
        objs[k] = _extract_balanced_dict(decompressed, start)
    return objs


def _decode_stream(data, obj_offset, dict_text):
    """Return the (FlateDecode-decompressed) stream payload of the object at
    ``obj_offset``. Only no-filter and /FlateDecode are supported."""
    stream_kw = data.find(b"stream", obj_offset)
    if stream_kw < 0:
        raise _ParseError("stream payload start not found")
    payload_start = stream_kw + len(b"stream")
    if data[payload_start:payload_start + 2] == b"\r\n":
        payload_start += 2
    elif data[payload_start:payload_start + 1] == b"\n":
        payload_start += 1
    else:
        raise _ParseError("stream payload prefix malformed")
    endstream_kw = data.find(b"endstream", payload_start)
    if endstream_kw < 0:
        raise _ParseError("endstream not found")
    payload_end = endstream_kw
    if data[payload_end - 2:payload_end] == b"\r\n":
        payload_end -= 2
    elif data[payload_end - 1:payload_end] == b"\n":
        payload_end -= 1
    payload = data[payload_start:payload_end]
    if len(payload) > MAX_XREF_STREAM_SIZE:
        raise _ParseError("stream payload too large")
    if "/FlateDecode" in dict_text:
        try:
            return zlib.decompress(payload)
        except zlib.error as e:
            raise _ParseError("flate decompress failed: %s" % e)
    if "/Filter" in dict_text:
        raise _ParseError("unsupported stream filter")
    return payload


def _extract_balanced_dict(data, start):
    """Return the first balanced ``<<...>>`` dict at/after ``start`` as a
    latin-1 string, or None. Counts nesting on << / >> tokens; sufficient
    for page-tree dicts (no literal angle-bracket pairs inside strings)."""
    if isinstance(data, str):
        data = data.encode("latin-1", "replace")
    i = data.find(b"<<", start)
    if i < 0:
        return None
    depth = 0
    j = i
    n = len(data)
    while j < n - 1:
        pair = data[j:j + 2]
        if pair == b"<<":
            depth += 1
            j += 2
        elif pair == b">>":
            depth -= 1
            j += 2
            if depth == 0:
                return data[i:j].decode("latin-1", "replace")
        else:
            j += 1
    return None


# --- minimal dict-value accessors (operate on the latin-1 dict text) ---


def _dict_ref(text, key):
    m = re.search(r"/%s\s+(\d+)\s+(\d+)\s+R\b" % key, text)
    return int(m.group(1)) if m else None


def _dict_int(text, key):
    m = re.search(r"/%s\s+(\d+)\b" % key, text)
    return int(m.group(1)) if m else None


def _dict_name(text, key):
    m = re.search(r"/%s\s*/([A-Za-z0-9.+-]+)" % key, text)
    return m.group(1) if m else None


def _dict_num_array(text, key):
    m = re.search(r"/%s\s*\[([^\]]*)\]" % key, text)
    if not m:
        return None
    try:
        vals = [float(x) for x in m.group(1).split()]
    except ValueError:
        return None
    return vals if len(vals) == 4 else None


def _dict_ref_array(text, key):
    m = re.search(r"/%s\s*\[([^\]]*)\]" % key, text)
    if not m:
        return []
    return [int(x) for x in re.findall(r"(\d+)\s+\d+\s+R\b", m.group(1))]


def _dict_ref_or_array(text, key):
    """/Contents may be a single ref or an array of refs."""
    m = re.search(r"/%s\s*\[" % key, text)
    if m:
        return _dict_ref_array(text, key)
    single = _dict_ref(text, key)
    return [single] if single is not None else []
