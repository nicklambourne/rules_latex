import { pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
const [oldRoot, newRoot] = process.argv.slice(2);
const old = await import(pathToFileURL(`${oldRoot}/latex/private/serve_web_chunks.js`));
const next = await import(pathToFileURL(`${newRoot}/latex/private/serve_web_chunks.js`));
const median = a => a.sort((a,b)=>a-b)[Math.floor(a.length/2)];
let total = 0;
for (const count of [100, 1000, 10000, 100000]) {
  const ranges = Array.from({length:count}, (_,i)=>({start:i*100,end:i*100+90,hash:String(i)}));
  const loops = 2000;
  const run = fn => { const start=performance.now(); for(let i=0;i<loops;i++){ const begin=((i*7919)%count)*100+50; total+=fn(ranges,begin,begin+20).length; } return (performance.now()-start)/loops*1000; };
  for(let i=0;i<5;i++){run(old.planRangeSegments);run(next.planRangeSegments);}
  const before=[],after=[];
  for(let i=0;i<21;i++){before.push(run(old.planRangeSegments));after.push(run(next.planRangeSegments));}
  console.log(JSON.stringify({objects:count,unit:'us/request',baseline:median(before),candidate:median(after)}));
}
if (!total) throw Error('unused results');
