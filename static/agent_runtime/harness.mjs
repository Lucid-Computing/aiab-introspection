// Pi core only: no coding-agent config loader, shell, skills, sessions or providers.
import { Agent } from '@earendil-works/pi-agent-core';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai/utils/event-stream';
import { obj, str } from '@earendil-works/pi-schema';
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';
const require = createRequire(import.meta.url);
const sandbox = require('./isolate.node');
const input = createInterface({input: process.stdin, crlfDelay: Infinity});
let seq = 0;
const pending = new Map();
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const rpc = (kind, payload) => new Promise((resolve, reject) => {
  const id = ++seq; pending.set(id, {resolve, reject}); send({id, kind, ...payload});
});
let started = false;
input.on('line', line => {
  try {
    if (line.length > 2097152) throw Error('frame_too_large');
    const value = JSON.parse(line);
    if (!started) { started = true; run(value).catch(() => {send({kind:'error', code:'agent_failed'}); process.exitCode=1; input.close();}); }
    else {
      const p = pending.get(value.id); if (!p) throw Error('unexpected_frame');
      pending.delete(value.id); value.error ? p.reject(Error(value.error)) : p.resolve(value.result);
    }
  } catch { process.exit(1); }
});
input.on('close', () => {for (const p of pending.values()) p.reject(Error('broker_closed'));});
// Evidence is sent by the supervisor ONLY after this attestation of installed isolation.
sandbox.install();
send({kind:'ready', protocol:'aiab-pi-broker/v1'});
async function run(init) {
  const tools = init.tools.map(t => ({
    name: t.name, label: t.name, description: t.description,
    parameters: obj({request: str({description:'JSON object of tool parameters'})}),
    execute: async (_id, args) => {
      const result = await rpc('tool', {name:t.name, arguments:JSON.parse(args.request)});
      return {content:[{type:'text',text:JSON.stringify(result)}],details:{}};
    }
  }));
  const agent = new Agent({
    initialState:{systemPrompt:init.system, model:{id:'deepseek-v4-1-flash',name:'DeepSeek',api:'aiab-broker',provider:'tinfoil',contextWindow:1048576,maxTokens:8192,reasoning:true,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0}},tools},
    toolExecution:'sequential',
    streamFn: (_model, context) => {
      const stream = createAssistantMessageEventStream();
      (async () => {
        let message = {role:'assistant',content:[],api:'aiab-broker',provider:'tinfoil',model:'deepseek-v4-1-flash',timestamp:Date.now(),usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason:'stop'};
        try {
          const result = await rpc('inference',{messages:context.messages});
          message = {...message,...result};
          stream.push({type:'start',partial:message});
          stream.push({type:'done',reason:message.stopReason,message});
        } catch {
          message.stopReason='error'; message.errorMessage='broker_denied';
          stream.push({type:'error',reason:'error',error:message});
        }
        stream.end(message);
      })();
      return stream;
    }
  });
  agent.subscribe(event => {
    // Private events cross only to the confidential supervisor.
    if (['turn_start','turn_end','tool_execution_start','tool_execution_end'].includes(event.type)) send({kind:'event',event});
  });
  await agent.prompt(init.prompt);
  const last = agent.state.messages.at(-1);
  if (!last || last.role !== 'assistant' || last.stopReason !== 'stop') throw Error('agent_failed');
  send({kind:'result',content:last.content});
  input.close();
}
