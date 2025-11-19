const { execSync } = require('child_process');

function parseMem(str){
  try{
    const m = str.match(/([0-9.]+)([KMG]i?)B/i);
    if(!m) return 0;
    const n = parseFloat(m[1]); const u = m[2].toUpperCase();
    const mul = u.startsWith('G')?1024*1024*1024: u.startsWith('M')?1024*1024:1024;
    return Math.round(n*mul);
  }catch(e){return 0}
}

async function gather(){
  const out = { mem:{}, swap:{}, containers:[] };
  try{
    const free = execSync('free -b').toString();
    const lines = free.split('\n');
    const memLine = lines.find(l=>l.toLowerCase().startsWith('mem:'))||'';
    const parts = memLine.split(/\s+/).filter(Boolean);
    if(parts.length>=3){ out.mem.total = parseInt(parts[1]||'0'); out.mem.used = parseInt(parts[2]||'0'); }
    const swapLine = lines.find(l=>l.toLowerCase().startsWith('swap:'))||'';
    const sp = swapLine.split(/\s+/).filter(Boolean);
    if(sp.length>=3){ out.swap.total = parseInt(sp[1]||'0'); out.swap.used = parseInt(sp[2]||'0'); }
  }catch(e){}
  try{
    const d = execSync("docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}'").toString();
    const lines = d.split('\n').filter(Boolean);
    for(const l of lines){
      const [name,cpu,memperc,memusage] = l.split('|');
      if(!name) continue;
      const memBytes = parseMem(memusage||'');
      out.containers.push({ name, cpu: parseFloat((cpu||'0').replace('%',''))||0, memperc: parseFloat((memperc||'0').replace('%',''))||0, memBytes });
    }
  }catch(e){}
  return out;
}

module.exports = { gather };
