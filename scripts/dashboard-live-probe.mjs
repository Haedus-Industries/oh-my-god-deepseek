/** Keep the short-lived verification credential out of argv, shell history and files. */
import {spawn} from "node:child_process";
import path from "node:path";
const terminal=process.stdin.isTTY;
const input=await new Promise((resolve,reject)=>{
  let value="";
  const finish=()=>{process.stdin.removeListener("data",data);process.stdin.pause();if(terminal)process.stdin.setRawMode(false);resolve(value);};
  const data=part=>{value+=part;if(value.includes("\u0003"))reject(new Error("cancelled"));else if(value.includes("\n")||value.includes("\r"))finish();};
  if(terminal)process.stdin.setRawMode(true);
  process.stderr.write("Ready for deployment probe JSON on stdin (input is hidden).\n");
  process.stdin.setEncoding("utf8");process.stdin.on("data",data);process.stdin.resume();
});
const python=path.resolve(process.platform==="win32"?".venv/Scripts/python.exe":".venv/bin/python");
const child=spawn(python,["-X","utf8","scripts/dashboard-live-probe.py"],{stdio:["pipe","inherit","inherit"]});
child.stdin.end(input.trim()+"\n");
child.on("error",()=>{console.error("Probe process unavailable");process.exitCode=1;});
child.on("exit",code=>{process.exitCode=code??1;});
