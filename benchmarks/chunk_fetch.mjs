// Controlled overlap benchmark: node benchmarks/chunk_fetch.mjs
// Counts server requests as well as completion time; this is not a browser
// render benchmark or a claim that every document has overlapping misses.
import { createServer } from 'node:http';
import { once } from 'node:events';
import { performance } from 'node:perf_hooks';
import { BoundedChunkCache, createChunkFetcher } from '../latex/private/serve_web_chunks.js';
const bytes = Buffer.alloc(1024 * 1024, 42);
let requests = 0;
const server = createServer((req, res) => {
  requests++;
  setTimeout(() => {
    res.writeHead(200, { 'Cache-Control': 'no-store', 'Content-Length': bytes.length });
    res.end(bytes);
  }, 20);
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const url = `http://127.0.0.1:${server.address().port}`;
const load = async hash => new Uint8Array(await (await fetch(`${url}/${hash}`)).arrayBuffer());
const baseline = cache => async hash => {
  const cached = cache.get(hash);
  if (cached) return cached;
  const result = await load(hash);
  cache.remember(hash, result);
  return result;
};
try {
  for (const consumers of [1, 8]) {
    const samples = { before: [], after: [] };
    for (let trial = 0; trial < 13; trial++) {
      for (const name of trial % 2 ? ['before', 'after'] : ['after', 'before']) {
        const cache = new BoundedChunkCache(1000, 32 * 1024 * 1024);
        const get = name === 'before' ? baseline(cache) : createChunkFetcher(cache, load);
        requests = 0;
        const start = performance.now();
        const result = await Promise.all(Array.from({ length: consumers }, () => get('hash')));
        if (result.some(buffer => buffer.length !== bytes.length)) throw Error('truncated');
        if (trial >= 2) samples[name].push({ms: performance.now() - start, requests});
      }
    }
    const median = values => values.sort((a,b)=>a-b)[Math.floor(values.length/2)];
    console.log(JSON.stringify({consumers, ...Object.fromEntries(Object.entries(samples).map(([name, values])=>[name, {ms:median(values.map(v=>v.ms)), requests:values[0].requests, mib:values[0].requests}]))}));
  }
} finally {
  server.closeAllConnections();
  server.close();
}
