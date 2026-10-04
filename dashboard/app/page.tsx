"use client";
/* Observer subscriptions hydrate saved state; no model control is exposed. */
/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Activity as ActivityIcon, Download, Radio, Terminal, WifiOff, Brain, MessageSquare, ChevronRight } from "lucide-react";
import { demoSnapshot, demoRecords } from "../lib/demo";
import { activities, type ActivityRow } from "../lib/activity";

type Outcome={status?:string;passed?:boolean|null;reason?:string;truncation?:string;attempt?:string;phase?:string};
type Attempt={slot:string;arm:string;phase:string;state:string;started_at:string;finished_at?:string;stage?:string;stages?:{phase:string;at:string}[];result?:Outcome};
type Amounts={settled_cny:number;reserved_cny:number;unknown_cny:number;requests:number};
type Snapshot={experiment_id:string;version:number;heartbeat_at?:string;status:string;finished:boolean;simulation?:boolean;source_commit?:string|null;fingerprint:string;accounting:Amounts&{remaining_cny:number;input_uncached:number;input_cached:number;output_tokens:number;trials?:Record<string,Amounts>};state:{execution_started_at?:string;finished_at?:string;results?:Record<string,Outcome>;attempts?:Record<string,Attempt>;third_schedule?:{arm:string}[];replacements?:number}};
type Artifact={stream:string;bytes:number;attempt:string};
const arms=[["B","Baseline"],["U","用户祷告"],["S","系统祷告"],["F","前沿身份"]];
const stages:Record<string,string>={preparing:"准备",agent:"模型工作",snapshot:"保存补丁",verifier:"判分",finished:"结束"};
const statuses:Record<string,string>={running:"进行中",completed:"已结束",execution_time_limit:"时间上限",interrupted_or_error:"已中断",archived:"已归档"};
const money=(v=0)=>`¥${v.toFixed(3)}`;
const clock=(v?:string)=>v?new Date(v).toLocaleTimeString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"—";
function duration(v:number){const n=Math.max(0,Math.floor(v));return `${Math.floor(n/3600)}:${String(Math.floor(n%3600/60)).padStart(2,"0")}:${String(n%60).padStart(2,"0")}`;}
function verdict(v?:Outcome,running=false){
  if(!v)return <span className="result empty">{running?"待判分":"待运行"}</span>;
  if(v.status==="scored")return <span className={`result ${v.passed?"pass":"fail"}`}>{v.passed?"通过":"不通过"}{v.truncation==="agent_time"&&<small>模型时间上限</small>}</span>;
  return <span className="result warning" title={v.reason}>{({budget_truncated:"预算截断",infrastructure_error:"基础设施错误"} as Record<string,string>)[v.status??""]??"未判分"}</span>;
}
async function read<T>(path:string,signal?:AbortSignal):Promise<T>{const r=await fetch(`/api/v1/${path}`,{cache:"no-store",signal});if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();}
export default function Home(){
  const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[experiment,setExperiment]=useState("");
  const [experiments,setExperiments]=useState<{id:string;simulation:boolean}[]>([]);
  const [attempt,setAttempt]=useState(""),[filter,setFilter]=useState("all"),[records,setRecords]=useState<ActivityRow[]>([]),[artifacts,setArtifacts]=useState<Artifact[]>([]);
  const [follow,setFollow]=useState(true),[more,setMore]=useState(false),[error,setError]=useState(false),[waiting,setWaiting]=useState(true);
  const [demo,setDemo]=useState(false),[demoMode,setDemoMode]=useState("running"),[now,setNow]=useState(()=>Date.now()),[page,setPage]=useState(0),[newActivity,setNewActivity]=useState(false);
  const [latestView,setLatestView]=useState(false),[historyPages,setHistoryPages]=useState([0]);
  const cursor=useRef(0),revision=useRef(""),pane=useRef<HTMLDivElement>(null),following=useRef(true),visibleCount=useRef(0),pageStarts=useRef([0]),loading=useRef(false),generation=useRef(0),tail=useRef(false),pageIndex=useRef(0);
  useEffect(()=>{setDemo(new URLSearchParams(window.location.search).get("demo")==="1");const t=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(t);},[]);
  useEffect(()=>{following.current=follow;},[follow]);
  useEffect(()=>{if(demo){const s=demoSnapshot(demoMode) as Snapshot;setSnapshot(s);setExperiment(s.experiment_id);setWaiting(false);setAttempt("S1-a1");setError(demoMode==="offline");}},[demo,demoMode]);
  useEffect(()=>{
    if(demo)return;let timer:ReturnType<typeof setTimeout>;const abort=new AbortController();
    async function poll(){try{const list=await read<{experiments:typeof experiments}>("experiments",abort.signal);setExperiments(list.experiments);const id=experiment||list.experiments[0]?.id;
      if(id){if(!experiment)setExperiment(id);const r=await read<{snapshot:Snapshot;revision:string}>(`snapshot?experiment=${id}`,abort.signal);
        if(revision.current!==r.revision){if(revision.current&&!following.current)setNewActivity(true);setSnapshot(r.snapshot);revision.current=r.revision;const a=await read<{artifacts:Artifact[]}>(`artifacts?experiment=${id}`,abort.signal);setArtifacts(a.artifacts);}}
      setError(false);setWaiting(false);
    }catch{if(!abort.signal.aborted){setError(true);setWaiting(false);}}if(!abort.signal.aborted)timer=setTimeout(poll,document.hidden?30000:5000);}
    const visible=()=>{if(!document.hidden){clearTimeout(timer);void poll();}};void poll();document.addEventListener("visibilitychange",visible);return()=>{abort.abort();clearTimeout(timer);document.removeEventListener("visibilitychange",visible);};
  },[experiment,demo]);
  const attempts=snapshot?.state.attempts??{},ids=Object.keys(attempts).sort((a,b)=>attempts[a].started_at.localeCompare(attempts[b].started_at));
  useEffect(()=>{if(!attempt&&ids.length)setAttempt(ids.find(id=>attempts[id].state==="running")??ids[0]);},[ids.join(","),attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  const load=useCallback(async(signal?:AbortSignal)=>{
    if(!experiment||!attempt||demo||loading.current)return;const run=generation.current;loading.current=true;
    const boundary=!tail.current?pageStarts.current[pageIndex.current+1]:undefined;
    try{const r=await read<{records:ActivityRow[];next_cursor:number;more:boolean}>(`events?experiment=${experiment}&attempt=${encodeURIComponent(attempt)}&cursor=${cursor.current}${boundary!==undefined?`&until=${boundary}`:""}`,signal);
      if(signal?.aborted||run!==generation.current)return;cursor.current=r.next_cursor;setMore(r.more);
      if(tail.current){setRecords(previous=>[...previous,...r.records].slice(-200));visibleCount.current=0;}
      else{visibleCount.current+=r.records.length;setRecords(previous=>[...previous,...r.records]);if(visibleCount.current>=200){setFollow(false);following.current=false;setNewActivity(true);}}
      return r.more;
    }finally{loading.current=false;}
  },[experiment,attempt,demo]);
  useEffect(()=>{generation.current++;cursor.current=0;visibleCount.current=0;pageStarts.current=[0];tail.current=false;setLatestView(false);setHistoryPages([0]);pageIndex.current=0;setPage(0);setRecords([]);setMore(false);setNewActivity(false);
    if(demo){setRecords(demoRecords(attempt));return;}let timer:ReturnType<typeof setTimeout>;const abort=new AbortController();let initial=true;
    async function poll(){try{if(initial||following.current){await load(abort.signal);initial=false;}}catch{}if(!abort.signal.aborted)timer=setTimeout(poll,document.hidden?30000:5000);}void poll();return()=>{abort.abort();clearTimeout(timer);};
  },[load,demo,attempt]);
  useEffect(()=>{if(follow&&pane.current)pane.current.scrollTop=pane.current.scrollHeight;},[records,follow]);
  async function turnPage(next:number){if(loading.current)return;tail.current=false;setLatestView(false);setFollow(false);following.current=false;generation.current++;if(next>page&&pageStarts.current[next]===undefined)pageStarts.current[next]=cursor.current;pageIndex.current=next;setHistoryPages([...pageStarts.current]);cursor.current=pageStarts.current[next];visibleCount.current=0;setPage(next);setRecords([]);await load();}
  async function latest(){if(demo){setFollow(true);return;}if(loading.current)return;const intent=++generation.current;tail.current=true;setLatestView(true);setFollow(false);following.current=false;try{let remaining=await load();while(generation.current===intent&&tail.current&&remaining)remaining=await load();if(generation.current===intent){setNewActivity(false);setFollow(true);following.current=true;}}catch{setError(true);}}
  const selected=attempts[attempt],accounting=snapshot?.accounting,results=snapshot?.state.results??{},started=snapshot?.state.execution_started_at;
  const elapsed=started?((snapshot?.state.finished_at?Date.parse(snapshot.state.finished_at):now)-Date.parse(started))/1000:0;
  const stale=!snapshot?.finished&&!!snapshot?.heartbeat_at&&now-Date.parse(snapshot.heartbeat_at)>120000;
  const completed=Object.values(results).filter(v=>v.phase==="primary").length;
  const activity=activities(records).filter(v=>filter==="all"||filter==="tools"?filter==="all"||v.channel.startsWith("tool_"):v.channel===filter);
  const download=(stream:string)=>`/api/v1/download?experiment=${experiment}&stream=${encodeURIComponent(stream)}`;
  const full=(kind:string)=>`/api/v1/download?experiment=${experiment}&attempt=${encodeURIComponent(attempt)}&channel=${kind}`;
  return <div className="workbench">
    <header className="masthead"><Link className="brand" href="/"><ActivityIcon size={20}/>DeepSeek 提示词实验</Link><a href="https://github.com/Haedus-Industries/oh-my-god-deepseek">公开源码 ↗</a></header>
    <main>
      <div className="title-row"><div><h1>effect-sse-httpapi-streaming</h1><p className="task-name">DeepSeek V4.1 Flash · DSH Minimal</p></div><div className={`live-state ${error||stale?"warning":""}`}>{error||stale?<WifiOff size={16}/>:<Radio size={16}/>}<span>{error?"连接异常":stale?"状态可能过期":snapshot?statuses[snapshot.status]??snapshot.status:waiting?"读取中":"等待 Cloud 上报"}<time title={snapshot?.heartbeat_at}>心跳 {clock(snapshot?.heartbeat_at)}</time></span></div></div>
      {(demo||snapshot?.simulation)&&<div className="notice"><span>模拟资料 · 不计入实验结果</span>{demo&&<label>演示状态 <select value={demoMode} onChange={e=>setDemoMode(e.target.value)}><option value="running">运行中</option><option value="offline">Cloud 断联</option><option value="budget">预算停止</option><option value="completed">实验结束</option></select></label>}</div>}
      {experiments.length>1&&<label className="experiment-picker">实验 <select value={experiment} onChange={e=>{setExperiment(e.target.value);setAttempt("");revision.current="";setSnapshot(null);}}>{experiments.map(v=><option key={v.id} value={v.id}>{v.id} · {v.simulation?"模拟":"正式"}</option>)}</select></label>}
      <section className="metrics" aria-label="进度与预算"><div><label>主要运行</label><strong>{completed}<span> / 8</span></strong></div><div><label>用时 / 24h</label><strong>{started?duration(elapsed):"—"}</strong></div><div><label>已结算估算</label><strong>{money(accounting?.settled_cny)}</strong></div><div><label>API 可用 / ¥160</label><strong>{money(accounting?.remaining_cny??160)}</strong></div></section>
      <section className="results"><div className="matrix-wrap"><table className="matrix"><thead><tr><th>提示条件</th><th>第一次</th><th>第二次</th><th className="third">第三次 · 追加</th></tr></thead><tbody>{arms.map(([arm,title])=><tr key={arm}><th><span className="arm-id">{arm}</span>{title}</th>{[1,2].map(n=><td key={n}>{results[`${arm}${n}`]?<button className="result-button" onClick={()=>{const id=results[`${arm}${n}`].attempt;if(id)setAttempt(id);}}>{verdict(results[`${arm}${n}`])}</button>:<span className="result empty">{Object.values(attempts).some(a=>a.slot===`${arm}${n}`&&a.state==="running")?"运行中":"待运行"}</span>}</td>)}<td className="third">{results[`${arm}3`]?verdict(results[`${arm}3`]):<span className="result empty">{snapshot?.state.third_schedule?.some(s=>s.arm===arm)?"待运行":snapshot?.finished?"未追加":"待定"}</span>}</td></tr>)}</tbody></table></div></section>
      <section className="workspace"><div className="workspace-heading"><h2>模型工作</h2>{ids.length>0&&<label className="attempt-picker"><span className="sr-only">选择运行</span><select value={attempt} onChange={e=>setAttempt(e.target.value)}>{ids.map(id=><option key={id} value={id}>{id} · {Number(id.split("-a")[1])>1?"基础设施替换":attempts[id].phase==="primary"?"主要比较":"一致性检查"} · {stages[attempts[id].stage??"preparing"]}</option>)}</select></label>}<div className="workspace-controls"><label className="follow"><input type="checkbox" checked={follow} onChange={e=>{generation.current++;setFollow(e.target.checked);following.current=e.target.checked;if(e.target.checked){tail.current=true;setLatestView(true);setNewActivity(false);}}}/>跟随</label><button className="plain-button" onClick={()=>void latest()}>回到最新{newActivity?" · 有更新":""}</button></div></div>
      {selected?<><div className="run-strip"><span>{selected.slot} · {stages[selected.stage??"preparing"]}</span><span>{duration(((selected.finished_at?Date.parse(selected.finished_at):now)-Date.parse(selected.started_at))/1000)}</span>{verdict(selected.result,true)}<details className="run-details"><summary>阶段与费用</summary><div>{(selected.stages??[]).map((s,i)=><p key={i}>{clock(s.at)} · {stages[s.phase]??s.phase}</p>)}<p>结束原因：{selected.result?.reason??"待结束"}</p><p>已结算 {money(accounting?.trials?.[attempt]?.settled_cny)} · 预留 {money(accounting?.trials?.[attempt]?.reserved_cny)} · 未知 {money(accounting?.trials?.[attempt]?.unknown_cny)}</p></div></details></div>
      <div className="activity-toolbar"><label><span className="sr-only">筛选活动</span><select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">全部活动</option><option value="reasoning">推理</option><option value="answer">回答</option><option value="tools">工具</option></select></label>{!demo&&<details className="downloads"><summary><Download size={14}/>下载全文</summary><div>{[["reasoning","推理"],["answer","回答"],["tool_call","工具调用"],["tool_result","工具输出"]].map(([v,label])=><a key={v} href={full(v)}>{label}</a>)}</div></details>}</div>
      <div ref={pane} className="activity-feed" aria-label="模型活动时间线" onWheel={e=>{if(e.deltaY<0){generation.current++;setFollow(false);following.current=false;}}} onTouchStart={()=>{generation.current++;setFollow(false);following.current=false;}}>
        {activity.length?activity.map(v=><article key={v.id} className={`activity-entry entry-${v.channel}`} data-channel={v.channel}><div className="activity-marker">{v.channel==="reasoning"?<Brain size={16}/>:v.channel==="answer"?<MessageSquare size={16}/>:<Terminal size={16}/>}</div><div className="activity-body">{v.channel==="tool_call"?<details className="tool-activity"><summary><ChevronRight size={14}/><span className="tool-name">{v.name}</span><code title={v.command}>{v.command}</code><time>{clock(v.at)}</time></summary><pre className="command">{v.command}</pre>{v.output?<pre className="tool-output">{v.output}</pre>:<p className="muted">输出待接收，或在其它分页中。</p>}<details className="raw-record"><summary>原始记录</summary><pre>{v.raw}</pre></details></details>:<><div className="activity-label"><span title={v.channel==="reasoning"?"API 返回的推理文本，不等于完整内部思维链":undefined}>{v.channel==="reasoning"?"推理":v.channel==="answer"?"回答":"工具输出"}</span><time title={v.request??undefined}>{clock(v.at)}</time></div><pre className="message-text">{v.content}</pre></>}</div></article>):<p className="empty-state">等待模型活动…</p>}
      </div>
      {!demo&&<div className="pagination"><span>{latestView?"最新记录":`第 ${page+1} 页`}{!follow?" · 已暂停跟随":""}</span>{latestView&&<button onClick={()=>void turnPage(0)}>从头阅读</button>}{page>0&&!latestView&&<button onClick={()=>void turnPage(page-1)}>上一页</button>}{more&&records.length<200&&!latestView&&<button onClick={()=>{setFollow(false);following.current=false;void load();}}>加载后续</button>}{(records.length>=200||historyPages.length>page+1)&&!latestView&&<button onClick={()=>void turnPage(page+1)}>下一页</button>}</div>}
      {artifacts.some(a=>a.attempt===attempt)&&<details className="run-artifacts"><summary>补丁与本次资料</summary><div className="artifact-list">{artifacts.filter(a=>a.attempt===attempt).map(a=><a key={a.stream} href={download(a.stream)}><span>{a.stream.split("/").at(-1)}</span><small>{(a.bytes/1024).toFixed(1)} KiB</small></a>)}</div></details>}</>:<div className="empty-state"><Terminal size={20}/>Cloud 开始执行后显示工作记录。</div>}
      </section>
      <details className="research-notes"><summary>实验说明与研究资料</summary><div className="notes-content"><p>真实模型为 deepseek-flash，固定 Minimal 工具、max 推理档位、1M 上下文、单请求最多 256,000 输出 token。U 将祷文放在 User；S 放在 System；F 只替换系统身份为 GPT-6 Astra。</p><p>固定前两次为主要比较；全部完成后，仅一过一不过的组追加第三次，一致性结果单列。官方 verifier 决定通过与否；预算截断、基础设施错误和未判分分别标记。基础设施替换 {snapshot?.state.replacements??0}/2。</p><p>API 已结算估算 {money(accounting?.settled_cny)}，预留 {money(accounting?.reserved_cny)}，费用未知 {money(accounting?.unknown_cny)}。预留与未知均占用额度，互不重复；usage 返回前用量待结算。总预算 ¥200，API ¥160，单次 ¥20。</p><div className="token-row"><span>非缓存输入 <b>{(accounting?.input_uncached??0).toLocaleString()}</b></span><span>缓存输入 <b>{(accounting?.input_cached??0).toLocaleString()}</b></span><span>输出 <b>{(accounting?.output_tokens??0).toLocaleString()}</b></span><span>请求 <b>{accounting?.requests??0}</b></span></div><p>推理栏为 API 返回的推理文本。时间来自评测侧接收记录与 SDK 事件；增量记录可能稍后到达。工具输出位于对应调用下；分页跨界时可单独出现，可下载全文复核。取消跟随或向上滚动可保留阅读位置。</p><p>单次 agent 上限 3 小时、判分 30 分钟、整轮 24 小时，并发 2。看板每 5 秒读取，隐藏时 30 秒；Cloud 每 10 秒上报，心跳超过 120 秒标为过期。看板只读，不提供实验干预。</p><div className="artifact-list">{artifacts.filter(a=>!a.attempt).map(a=><a key={a.stream} href={download(a.stream)}><Download size={14}/>{a.stream}<small>{(a.bytes/1024).toFixed(1)} KiB</small></a>)}</div><p>实验 {snapshot?.experiment_id??"待创建"}<br/>源码 {snapshot?.source_commit??"尚未绑定"}<br/>资源／协议指纹 {snapshot?.fingerprint??"—"}</p><p>原始资料留在 Cloud，公开副本经过脱敏；隐藏判分资源不进入上报。首轮结论仅适用于本任务与资源配置。公开历史只用于选题。</p><a href="?demo=1">查看模拟页面</a></div></details>
    </main><footer>DeepSeek 提示词探索实验<span>公开只读 · Asia/Shanghai</span></footer>
  </div>;
}
