const { execSync } = require('child_process');
const fs = require('fs');
const LOG = '/var/blobe/logs/optimizer/optimizer.log';
function log(msg){ try{ fs.appendFileSync(LOG, `[${new Date().toISOString()}] SWAP: ${msg}\n`); }catch(e){} }

async function check(cfg){
  try{
    // Check swap usage via free
    const out = execSync('free -b').toString();
    const lines = out.split('\n');
    const swapLine = lines.find(l=>l.toLowerCase().startsWith('swap'));
    if(swapLine){
      const parts = swapLine.split(/\s+/).filter(Boolean);
      const total = parseInt(parts[1]||'0')||0;
      const used = parseInt(parts[2]||'0')||0;
      const perc = total ? Math.round(used/total*100) : 0;
      if(perc >= (cfg.swapThreshold || 10)){
        // Restart heaviest VM by memory
        const stats = execSync("docker stats --no-stream --format \"{{.Name}} {{.MemUsage}}\"").toString();
        const lines2 = stats.split('\n').filter(Boolean);
        let heaviest = null, maxBytes=0;
        for(const l of lines2){
          const p = l.trim().split(/\s+/);
          const name = p[0];
          if(!name.startsWith('blobevm_')) continue;
          // MemUsage like "12.3MiB / 1.944GiB"
          const usage = p[1] || '0';
          const m = usage.match(/([0-9.]+)([KMG]i?)B/i);
          let bytes = 0;
          if(m){
            const n = parseFloat(m[1]); const u = m[2].toUpperCase();
            const mul = u.startsWith('G')?1024*1024*1024: u.startsWith('M')?1024*1024:1024;
            bytes = Math.round(n*mul);
          }
          if(bytes>maxBytes){ maxBytes=bytes; heaviest=name; }
        }
        // Drop caches
        try{ execSync('sync; echo 3 > /proc/sys/vm/drop_caches'); }catch(e){}
        if(heaviest){ log(`Restarting ${heaviest} due to swap ${perc}%`); try{ execSync(`docker restart ${heaviest}`); }catch(e){} }
        return {action:'restart', reason:'swap', perc, heaviest};
      }
    }
  }catch(e){ log('swapguard error '+e); }
  return null;
}

module.exports = { check };
