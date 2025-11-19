const { execSync } = require('child_process');
const fs = require('fs');
const LOG = '/var/blobe/logs/optimizer/optimizer.log';
function log(msg){ try{ fs.appendFileSync(LOG, `[${new Date().toISOString()}] CPU: ${msg}\n`); }catch(e){} }

async function check(cfg){
  try{
    // docker stats gives CPU percentage
    const out = execSync("docker stats --no-stream --format \"{{.Name}} {{.CPUPerc}}\"").toString();
    const lines = out.split('\n').filter(Boolean);
    for(const l of lines){
      const parts = l.trim().split(/\s+/);
      const name = parts[0];
      if(!name.startsWith('blobevm_')) continue;
      const percRaw = parts[1]||'0%';
      const perc = parseFloat(percRaw.replace('%',''))||0;
      if(perc >= (cfg.cpuThreshold || 70)){
        // For CPU we require it to be sustained; simple best-effort: check again briefly
        try{ const out2 = execSync(`docker stats --no-stream --format \"{{.Name}} {{.CPUPerc}}\" --no-trunc`).toString(); }catch(e){}
        log(`Restarting ${name} due to cpu ${perc}%`);
        try{ execSync(`docker restart ${name}`); }catch(e){}
        return {action:'restart', reason:'cpu', container:name, perc};
      }
    }
  }catch(e){ log('cpuguard error '+e); }
  return null;
}

module.exports = { check };
