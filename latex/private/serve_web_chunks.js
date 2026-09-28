// serve_web_chunks.js — pure PDF byte-range assembly planning for the
// live-preview client's ChunkedTransport, factored out of serve_web.js so
// the fiddly gap/overlap/past-last-chunk logic can be unit-tested without
// PDF.js or the network. serve_web.js does the actual fetching.

// Plan how to assemble the byte range [begin, end) of the PDF from a list
// of content-addressed chunk ranges (each `{ start, end, hash }`, sorted
// by start and non-overlapping), filling the gaps not covered by any chunk — the PDF
// "skeleton" (header, xref, trailer) — with skeleton segments.
//
// Returns an ordered list of segment descriptors:
//   { kind: "chunk",    hash, sliceStart, sliceEnd }  // slice of a chunk
//   { kind: "skeleton", begin, end }                  // fetched from /pdf
// The caller fetches each and concatenates. Half-open ranges throughout.
export function planRangeSegments(sortedRanges, begin, end) {
  const segments = [];
  let cursor = begin;
  // Find the first overlapping chunk in O(log n). PDF.js often requests
  // ranges near the end of large manifests; don't scan from the header
  // on every request. Non-overlapping ranges have monotonically rising ends.
  let i = 0;
  let high = sortedRanges.length;
  while (i < high) {
    const mid = i + Math.floor((high - i) / 2);
    if (sortedRanges[mid].end <= begin) i = mid + 1;
    else high = mid;
  }
  while (cursor < end) {
    if (i < sortedRanges.length && sortedRanges[i].start < end) {
      const r = sortedRanges[i];
      if (cursor < r.start) {
        // Skeleton gap before this chunk.
        const gapEnd = Math.min(r.start, end);
        segments.push({ kind: "skeleton", begin: cursor, end: gapEnd });
        cursor = gapEnd;
      } else {
        // Inside / overlapping the chunk — emit the covered slice.
        const sliceStart = cursor - r.start;
        const sliceEnd = Math.min(end, r.end) - r.start;
        segments.push({ kind: "chunk", hash: r.hash, sliceStart, sliceEnd });
        cursor = r.start + sliceEnd;
        if (cursor >= r.end) i++;
      }
    } else {
      // Past the last chunk that overlaps [begin, end) — pure skeleton.
      segments.push({ kind: "skeleton", begin: cursor, end });
      cursor = end;
    }
  }
  return segments;
}

// In-memory chunks need a byte ceiling as well as an entry ceiling: a
// handful of large PDF objects can otherwise retain most of a PDF.
export class BoundedChunkCache extends Map {
  constructor(maxEntries, maxBytes) {
    super();
    this.maxEntries = maxEntries;
    this.maxBytes = maxBytes;
    this.byteSize = 0;
  }

  remember(hash, bytes) {
    const previous = this.get(hash);
    if (previous) {
      this.byteSize -= previous.byteLength;
      this.delete(hash);
    }
    if (bytes.byteLength > this.maxBytes) return;
    while (this.size &&
           (this.size >= this.maxEntries ||
            this.byteSize + bytes.byteLength > this.maxBytes)) {
      const oldest = this.keys().next().value;
      this.byteSize -= this.get(oldest).byteLength;
      this.delete(oldest);
    }
    this.set(hash, bytes);
    this.byteSize += bytes.byteLength;
  }
}
