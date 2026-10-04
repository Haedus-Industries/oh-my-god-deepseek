export type ActivityRow = { id:string; seq:number; channel:string; source_at:string; request_id?:string|null; stream?:string; event_id?:string; part?:number; content:string };
export type Activity = { id:string; channel:string; at:string; request?:string|null; content:string; name?:string; command?:string; callId?:string; output?:string; raw?:string };
type ToolData = {callId?:string;name?:string;arguments?:unknown;message?:{source?:{callId?:string};content?:unknown}};
function parse(text:string):ToolData {try{return JSON.parse(text);}catch{return {};}}
function text(value:unknown):string {
  if(typeof value==="string")return value;
  if(Array.isArray(value))return value.map(text).join("\n");
  if(value&&typeof value==="object") {const v=value as {text?:string;content?:unknown};return v.text??(v.content!==undefined?text(v.content):JSON.stringify(value,null,2));}
  return value===undefined?"":JSON.stringify(value,null,2);
}
export function activities(records:ActivityRow[]):Activity[] {
  const grouped=new Map<string,ActivityRow[]>();
  for(const row of records) {const key=row.event_id?`${row.stream}:${row.channel}:${row.event_id}`:row.id;const group=grouped.get(key)??[];group.push(row);grouped.set(key,group);}
  const rows=[...grouped.values()].map(parts=>({...parts[0],content:parts.sort((a,b)=>(a.part??0)-(b.part??0)||a.seq-b.seq).map(p=>p.content).join("")})).sort((a,b)=>Date.parse(a.source_at)-Date.parse(b.source_at)||a.seq-b.seq);
  const result:Activity[]=[], calls=new Map<string,Activity>();
  for(const row of rows) {
    const data=parse(row.content), callId=data.callId??data.message?.source?.callId;
    const key=`${row.request_id??""}:${callId??row.id}`;
    if(row.channel==="tool_result") {
      const call=calls.get(key);
      const output=text(data.message?.content??row.content);
      if(call){call.output=(call.output??"")+output;call.raw=(call.raw??"")+"\n"+row.content;continue;}
      result.push({id:row.id,channel:row.channel,at:row.source_at,request:row.request_id,content:output,callId});continue;
    }
    if(row.channel==="tool_call") {
      let args=data.arguments;if(typeof args==="string"){try{args=JSON.parse(args);}catch{}}
      const value=args as {command?:string;path?:string}|undefined;
      const call={id:row.id,channel:row.channel,at:row.source_at,request:row.request_id,content:row.content,name:data.name??"工具调用",command:value?.path?`${value.command??"编辑"} ${value.path}`:value?.command??text(args),callId,raw:row.content};
      result.push(call);calls.set(key,call);continue;
    }
    const previous=result.at(-1);
    if(previous?.channel===row.channel&&previous.request===row.request_id&&["reasoning","answer"].includes(row.channel)){previous.content+=row.content;continue;}
    result.push({id:row.id,channel:row.channel,at:row.source_at,request:row.request_id,content:row.content});
  }
  return result;
}
