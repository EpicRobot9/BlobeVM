const { execSync } = require('child_process');
const fs = require('fs');
const LOG = '/var/blobe/logs/optimizer/optimizer.log';
function log(msg){ try{ fs.appendFileSync(LOG, `[${new Date().toISOString()}] HEALTH: ${msg}\n`); }catch(e){} }

// Multi-level health check: 1 warn, 2 restart container, 3 restart VM system, 5 redeploy container
async function check(cfg){
  try{
    // Check simple HTTP health for each VM using manager url
    const out = execSync('blobe-vm-manager list || true').toString();
    const lines = out.split('\n').map(l=>l.trim()).filter(l=>l.startsWith('- '));
    for(const l of lines){
      try{
        const parts = l.substring(2).split('->');
        const name = parts[0].trim().split()[0];
        const url = (parts[2]||'').trim();
        if(!url) continue;
        // Simple HEAD via curl
        try{
          const r = execSync(`curl -Is --max-time 6 ${url} | head -n1`).toString();
          if(!/HTTP\/(1|2) [23]../.test(r)){
            // escalate via flag files in instances dir
            const stateDir = process.env.BLOBEDASH_STATE || '/opt/blobe-vm';
            const f1 = `${stateDir}/instances/${name}/.health_warn`;
            const f2 = `${stateDir}/instances/${name}/.health_fail`;
            if(!fs.existsSync(f1)){
              log(`Health warn for ${name}`); fs.writeFileSync(f1, Date.now().toString());
              return {action:'warn', name};
            }
            if(!fs.existsSync(f2)){
              log(`Health restart container ${name}`); fs.writeFileSync(f2, Date.now().toString()); execSync(`docker restart blobevm_${name}`); return {action:'restart_container', name};
            }
            // On repeated failures escalate: recreate via manager
            log(`Health recreate ${name}`);
            try{ execSync(`blobe-vm-manager recreate ${name}`); }catch(e){}
            return {action:'recreate', name};
          }
        }catch(e){
          // unresponsive
          const stateDir = process.env.BLOBEDASH_STATE || '/opt/blobe-vm';
          const f1 = `${stateDir}/instances/${name}/.health_warn`;
          if(!fs.existsSync(f1)){ log(`Health warn (curl fail) for ${name}`); fs.writeFileSync(f1, Date.now().toString()); return {action:'warn', name}; }
          log(`Health restart container (curl fail) ${name}`); execSync(`docker restart blobevm_${name}`); return {action:'restart_container', name};
        }
      }catch(e){ }
    }
  }catch(e){ log('healthguard error '+e); }
  return null;
}

module.exports = { check };
