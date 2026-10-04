/** UI-only simulation; never ingested into the research dataset. */
export function demoSnapshot(mode:string) {
  const now=Date.now(), start=new Date(now-6240000).toISOString();
  const attempts:Record<string,unknown>={}, results:Record<string,unknown>={};
  for(const [i,arm] of ["B","U","S","F"].entries()) {
    const id=`${arm}1-a1`, ended=i<2||["completed","budget"].includes(mode);
    const result=mode==="budget"&&i>=2?{status:"budget_truncated",passed:null,reason:"budget_reservation_denied"}:{status:"scored",passed:i===1,reason:"finished"};
    attempts[id]={slot:`${arm}1`,arm,phase:"primary",state:ended?"complete":"running",started_at:start,finished_at:ended?new Date(now-600000).toISOString():undefined,stage:ended?"finished":"agent",stages:[{phase:"preparing",at:start},{phase:"agent",at:new Date(now-6000000).toISOString()},...(ended?[{phase:"snapshot",at:new Date(now-900000).toISOString()},{phase:"verifier",at:new Date(now-800000).toISOString()},{phase:"finished",at:new Date(now-600000).toISOString()}]:[])],result:ended?result:undefined};
    if(ended)results[`${arm}1`]={...result,attempt:id,phase:"primary"};
  }
  return {experiment_id:"simulation-only",version:1,simulation:true,heartbeat_at:new Date(now-(mode==="offline"?200000:4000)).toISOString(),status:["completed","budget"].includes(mode)?"completed":"running",finished:["completed","budget"].includes(mode),fingerprint:"simulation",source_commit:null,state:{execution_started_at:start,finished_at:["completed","budget"].includes(mode)?new Date(now).toISOString():undefined,attempts,results,replacements:0},accounting:{settled_cny:12.486,reserved_cny:mode==="budget"?0:5.2,unknown_cny:0,remaining_cny:mode==="budget"?.8:142.314,requests:24,input_uncached:92000,input_cached:184000,output_tokens:52000,trials:{}}};
}
export function demoRecords(attempt:string,channel="all") {
  const base=Date.now()-90000;
  const lines=[
    {channel:"reasoning",content:"先追踪 HTTP 响应的构造路径，确认流式数据是否被提前缓冲。"},
    {channel:"tool_call",content:JSON.stringify({callId:"demo-call-1",name:"bash",arguments:{command:"rg -n 'SSE|Stream' packages/platform/src"}})},
    {channel:"tool_result",content:JSON.stringify({message:{source:{callId:"demo-call-1"},content:[{text:"packages/platform/src/HttpServerResponse.ts:421: export const stream = ...\npackages/platform/src/HttpServerRequest.ts:208: body: Stream<Uint8Array>"}]}})},
    {channel:"reasoning",content:"响应的 body 仍是 Stream。接下来检查取消、错误传播和资源释放，避免在 SSE 连接中提前结束作用域。"},
    {channel:"tool_call",content:JSON.stringify({callId:"demo-call-2",name:"str_replace_editor",arguments:{command:"view",path:"packages/platform/src/HttpServerResponse.ts"}})},
    {channel:"tool_result",content:JSON.stringify({message:{source:{callId:"demo-call-2"},content:[{text:"421  export const stream = <E>(body: Stream<Uint8Array, E>, options?: Options) => ..."}]}})},
    {channel:"answer",content:"已定位流式响应的构造路径，正在检查资源释放并运行原有项目测试。"},
    {channel:"tool_call",content:JSON.stringify({callId:"demo-call-3",name:"bash",arguments:{command:"pnpm test -- HttpServerResponse"}})},
    {channel:"tool_result",content:JSON.stringify({message:{source:{callId:"demo-call-3"},content:[{text:"模拟测试输出：3 passed. 尚未执行官方 verifier。"}]}})},
    {channel:"reasoning",content:"项目测试通过不能替代官方判分，还需要核对错误和取消路径。"},
  ];
  return lines.map((r,i)=>({...r,id:`${attempt}-${i}`,seq:i+1,source_at:new Date(base+i*7000).toISOString(),request_id:`simulation-request-${Math.floor(i/3)}`})).filter(r=>channel==="all"||channel==="tools"?channel==="all"||r.channel.startsWith("tool_"):r.channel===channel);
}
