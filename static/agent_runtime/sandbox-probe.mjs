// Security regression probe. Trusted imports are complete before isolation.
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { Socket } from 'node:net';
import { createRequire } from 'node:module';
const sandbox = createRequire(import.meta.url)('./isolate.node');
sandbox.install();
const results = {installed:true};
try { readFileSync('/etc/passwd'); results.fileDenied=false; }
catch (e) { results.fileDenied=e.code==='EPERM'; }
results.execDenied=spawnSync('/bin/true').error?.code==='EPERM';
const socket = new Socket();
socket.on('error', e => {
  results.networkDenied=e.code==='EPERM';
  process.stdout.write(JSON.stringify(results)+'\n');
});
socket.connect(443,'127.0.0.1');
