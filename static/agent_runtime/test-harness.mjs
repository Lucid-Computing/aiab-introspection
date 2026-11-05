import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('kernel denies file opens, process execution and network after isolation', async () => {
  const child = spawn(process.execPath, ['--jitless',fileURLToPath(new URL('./sandbox-probe.mjs',import.meta.url))],{env:{},stdio:['ignore','pipe','pipe']});
  let output='', error='';
  child.stdout.on('data', b => output+=b);
  child.stderr.on('data', b => error+=b);
  const code = await new Promise(resolve=>child.on('close',resolve));
  assert.equal(code,0,error);
  assert.deepEqual(JSON.parse(output),{installed:true,fileDenied:true,execDenied:true,networkDenied:true});
});
