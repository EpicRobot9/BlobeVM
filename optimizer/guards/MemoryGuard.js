const { execSync } = require('child_process');
const fs = require('fs');
const LOG = '/var/blobe/logs/optimizer/optimizer.log';
function log(msg){ try{ fs.appendFileSync(LOG, `[${new Date().toISOString()}] MEM: ${msg}\n`); }catch(e){} }

async function check(cfg){
  try{
    // Use docker stats to find blobevm_ containers and their mem perc
    const out = execSync("docker stats --no-stream --format \"{{.Name}} {{.MemPerc}} {{.MemUsage}}\"").toString();
    const lines = out.split('\n').filter(Boolean);
    for(const l of lines){
      const parts = l.trim().split(/\s+/);
      const name = parts[0];
      if(!name.startsWith('blobevm_')) continue;
      const percRaw = parts[1]||'0%';
      const perc = parseFloat(percRaw.replace('%',''))||0;
      if(perc >= (cfg.memoryThreshold || 60)){
        // Restart container
        log(`Restarting ${name} due to memory ${perc}%`);
        try{ execSync(`docker restart ${name}`); }catch(e){}
        return {action:'restart', reason:'memory', container:name, perc};
      }
    }
  }catch(e){ log('memguard error '+e); }
  return null;
}

module.exports = { check };
