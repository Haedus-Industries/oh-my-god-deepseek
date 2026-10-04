import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { build } from "esbuild";
import { Miniflare } from "miniflare";

const started=Date.now(), tests=[];
const token=randomBytes(32).toString("hex"), sha=text=>createHash("sha256").update(text).digest("hex");
const output=await build({stdin:{contents:'import {benchApi} from "./lib/bench-api.ts"; export default {fetch:benchApi};',resolveDir:process.cwd()},bundle:true,write:false,format:"esm",platform:"browser"});
const mf=new Miniflare({modules:true,script:output.outputFiles[0].text,compatibilityDate:"2026-05-15",d1Databases:["DB"],r2Buckets:["BUCKET"],bindings:{DSBENCH_UPLOAD_HASHES:JSON.stringify({research:sha(token)})}});
const db=await mf.getD1Database("DB");
for(const sql of readFileSync("drizzle/0000_married_killraven.sql","utf8").split("--> statement-breakpoint")) await db.prepare(sql.trim()).run();
const request=(route,body,credential=token)=>mf.dispatchFetch(`https://dashboard.test/api/v1/${route}`,body?{method:"POST",headers:{Authorization:`Bearer ${credential}`,"Content-Type":"application/json"},body:JSON.stringify(body)}:undefined);
const chunk=(seq,text,extra={})=>({schema_version:1,experiment_id:"research",id:sha(`research-${seq}`),seq,attempt:"B1-a1",slot:"B1",stream:"B1-a1/reasoning",channel:"reasoning",source_at:"2026-10-04T00:00:00Z",content:text,sha256:sha(text),redacted:true,...extra});
async function test(name,run){await run();tests.push({name,passed:true});}
try{
 await test("public reads and experiment-scoped auth",async()=>{assert.equal((await request("health")).status,200);assert.equal((await request("check",{schema_version:1},"wrong")).status,401);assert.equal((await request("snapshot",{schema_version:1,experiment_id:"other",version:1})).status,403);});
 await test("new state never replaced by older state",async()=>{const s={schema_version:1,experiment_id:"research",source_at:"2026-10-04T00:00:00Z",version:2,status:"running"};assert.equal((await request("snapshot",s)).status,200);assert.equal((await request("snapshot",{...s,version:1,status:"old"})).status,200);assert.equal((await(await request("snapshot?experiment=research")).json()).snapshot.version,2);assert.equal((await request("snapshot",{...s,status:"conflict"})).status,409);});
 await test("duplicate chunks safe; conflicting content rejected",async()=>{const batch={schema_version:1,experiment_id:"research",chunks:[chunk(1,"first🙂")]};assert.equal((await request("chunks",batch)).status,200);assert.equal((await request("chunks",batch)).status,200);assert.equal((await request("chunks",{...batch,chunks:[chunk(1,"different")]})).status,409);const events=await(await request("events?experiment=research&attempt=B1-a1&channel=reasoning")).json();assert.equal(events.records.length,1);});
 await test("late out-of-order data remains reachable by cursor",async()=>{await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(3,"third")]});const initial=await(await request("events?experiment=research&channel=reasoning")).json();await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(2,"second")]});const later=await(await request(`events?experiment=research&channel=reasoning&cursor=${initial.next_cursor}`)).json();assert.equal(later.records[0].content,"second");const full=await request("download?experiment=research&attempt=B1-a1&channel=reasoning");assert.equal(await full.text(),"first🙂secondthird");});
 await test("large UTF8 log complete through paging and download",async()=>{const source="汉🙂\n".repeat(8000);for(let i=4;i<64;i++)assert.equal((await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(i,source)]})).status,200);let cursor=0,total=0;while(true){const page=await(await request(`events?experiment=research&channel=reasoning&cursor=${cursor}`)).json();total+=page.records.length;cursor=page.next_cursor;if(!page.more)break;}assert.equal(total,63);const full=await request("download?experiment=research&attempt=B1-a1&channel=reasoning");assert.equal(await full.text(),"first🙂secondthird"+source.repeat(60));});
 await test("oversized data and forged hash rejected",async()=>{assert.equal((await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(100,"x".repeat(65537))]})).status,400);assert.equal((await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(100,"abc",{sha256:"0".repeat(64)})]})).status,400);});
 await test("model markup remains inert text",async()=>{const text="<script>window.hacked=1</script>";await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(100,text,{stream:"unsafe.html",channel:"artifact"})]});const full=await request("download?experiment=research&stream=unsafe.html");assert.match(full.headers.get("Content-Type"),/^text\/plain/);assert.match(full.headers.get("Content-Disposition"),/^attachment/);assert.equal(await full.text(),text);});
 await test("concurrent conflicts cannot corrupt stored content",async()=>{
   const responses=await Promise.all([chunk(1000,"winner-a"),chunk(1000,"winner-b")].map(value=>request("chunks",{schema_version:1,experiment_id:"research",chunks:[value]})));
   assert.deepEqual(responses.map(r=>r.status).sort(),[200,409]);
   let cursor=0;const all=[];
   while(true){const next=await(await request(`events?experiment=research&channel=reasoning&cursor=${cursor}`)).json();all.push(...next.records);cursor=next.next_cursor;if(!next.more)break;}
   const stored=all.find(r=>r.seq===1000);assert.ok(["winner-a","winner-b"].includes(stored.content));assert.equal(sha(stored.content),stored.sha256);
   const state={schema_version:1,experiment_id:"research",source_at:"2026-10-04T00:00:00Z",version:3};
   const states=await Promise.all([request("snapshot",{...state,status:"a"}),request("snapshot",{...state,status:"b"})]);
   assert.deepEqual(states.map(r=>r.status).sort(),[200,409]);
 });
 const module=await build({entryPoints:["lib/activity.ts"],bundle:true,write:false,format:"esm",platform:"node"});
 await test("historical page boundary survives new arrivals",async()=>{
   const first=await(await request("events?experiment=research&channel=reasoning")).json();
   const bound=first.next_cursor;
   await request("chunks",{schema_version:1,experiment_id:"research",chunks:[chunk(2000,"new arrival")]});
   const old=await(await request(`events?experiment=research&channel=reasoning&until=${bound}`)).json();
   assert.deepEqual(old.records.map(r=>r.id),first.records.map(r=>r.id));
   assert.equal(old.next_cursor,bound);
 });
 const {activities}=await import(`data:text/javascript;base64,${Buffer.from(module.outputFiles[0].text).toString("base64")}`);
 await test("activity follows source time and pairs tools with output",async()=>{
   const row=(id,channel,second,content)=>({id,seq:second,channel,source_at:`2026-10-04T00:00:0${second}Z`,request_id:"request-1",content});
   const rows=[row("answer","answer",4,"done"),row("result","tool_result",3,JSON.stringify({message:{source:{callId:"call"},content:[{text:"output"}]}})),row("thought","reasoning",1,"check"),row("call","tool_call",2,JSON.stringify({callId:"call",name:"bash",arguments:'{"command":"ls"}'}))];
   const timeline=activities(rows);assert.deepEqual(timeline.map(v=>v.channel),["reasoning","tool_call","answer"]);assert.equal(timeline[1].command,"ls");assert.equal(timeline[1].output,"output");
 });
 await test("fragmented tool output reconstructs complete text",async()=>{
   const output=JSON.stringify({message:{source:{callId:"call"},content:[{text:"完整🙂 output"}]}});
   const call={id:"call",seq:1,channel:"tool_call",source_at:"2026-10-04T00:00:01Z",request_id:"r",content:JSON.stringify({callId:"call",name:"bash",arguments:"{}"})};
   const chunks=[0,1].map(part=>({id:`part-${part}`,seq:part+2,channel:"tool_result",source_at:"2026-10-04T00:00:02Z",request_id:"r",stream:"tools",event_id:"event",part,content:part?output.slice(30):output.slice(0,30)}));
   assert.equal(activities([call,...chunks])[0].output,"完整🙂 output");
 });
}finally{await mf.dispose();mkdirSync("outputs",{recursive:true});const summary={tests,passed:tests.length,elapsed_ms:Date.now()-started};writeFileSync("outputs/api-tests.json",JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));}
