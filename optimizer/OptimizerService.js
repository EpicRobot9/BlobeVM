#!/usr/bin/env node
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const guards = require('./guards');
const utils = require('../utils/systemStats');

const STATE_DIR = process.env.BLOBEDASH_STATE || '/opt/blobe-vm';
const LOG_DIR = '/var/blobe/logs/optimizer';
const CFG_PATH = path.join(STATE_DIR, '.optimizer.json');

function ensureLogDir(){
  try{ fs.mkdirSync(LOG_DIR, { recursive: true }); }catch(e){}
}

function log(msg){
  ensureLogDir();
  const ts = new Date().toISOString();
  const line = `[${ts}] ${msg}\n`;
  fs.appendFileSync(path.join(LOG_DIR, 'optimizer.log'), line);
}

function loadConfig(){
  try{ return JSON.parse(fs.readFileSync(CFG_PATH)); }catch(e){
    return { enabled: true, guards: { memory:true, cpu:true, swap:true, health:true }, schedulerEnabled:true, restartIntervalHours:24, strictMemoryLimit:false, memoryLimit:'1g', memorySwappiness:10, containerRestartCooldownMinutes:10 };
  }
}

function saveConfig(cfg){
  try{ fs.writeFileSync(CFG_PATH, JSON.stringify(cfg, null, 2)); return true; }catch(e){ return false; }
}

async function gather(){
  const stats = await utils.gather();
  return stats;
}

async function runOnce(){
  const cfg = loadConfig();
  const events = [];
  try{
    if(cfg.guards && cfg.guards.memory){
      const r = await guards.MemoryGuard.check(cfg);
      if(r) events.push(r);
    }
    if(cfg.guards && cfg.guards.cpu){
      const r = await guards.CPUGuard.check(cfg);
      if(r) events.push(r);
    }
    if(cfg.guards && cfg.guards.swap){
      const r = await guards.SwapGuard.check(cfg);
      if(r) events.push(r);
    }
    if(cfg.guards && cfg.guards.health){
      const r = await guards.HealthGuard.check(cfg);
      if(r) events.push(r);
    }
    // Enforce strict memory limits if configured
    if(cfg.strictMemoryLimit){
      try{
        enforceStrictMemory(cfg);
      }catch(e){ log('error enforcing strictMemoryLimit: '+e); }
    }
  }catch(e){ log('error in runOnce: '+e); }
  return events;
}

function enforceStrictMemory(cfg){
  // Apply docker update to all blobevm_ containers to enforce memory limits
  try{
    const out = require('child_process').execSync("docker ps --format '{{.Names}}' ").toString();
    const lines = out.split('\n').map(l=>l.trim()).filter(Boolean);
    for(const name of lines){
      if(!name.startsWith('blobevm_')) continue;
      const mem = cfg.memoryLimit || '1g';
      const swappiness = (cfg.memorySwappiness !== undefined) ? cfg.memorySwappiness : 10;
      try{
        // docker update accepts --memory and --memory-swap and --memory-swappiness
        require('child_process').execSync(`docker update --memory=${mem} --memory-swap=${mem} --memory-swappiness=${swappiness} ${name}`);
        log(`enforce memory on ${name} -> ${mem} swappiness=${swappiness}`);
      }catch(e){ log('docker update failed for '+name+' : '+e); }
    }
  }catch(e){ log('enforceStrictMemory error '+e); }
}

function getLastRestartPath(){
  return path.join(STATE_DIR, '.optimizer_last_restart');
}

function readLastRestart(){
  try{ const p = getLastRestartPath(); return parseInt(fs.readFileSync(p,'utf8')) || 0; }catch(e){ return 0; }
}

function writeLastRestart(ts){
  try{ fs.writeFileSync(getLastRestartPath(), String(ts)); return true; }catch(e){ return false; }
}

function performScheduledRestart(cfg){
  try{
    const last = readLastRestart();
    const now = Date.now()/1000;
    const interval = (cfg.restartIntervalHours || 0) * 3600;
    if(!cfg.schedulerEnabled || !interval) return;
    if(now - last < interval) return;
    // restart running blobevm_ containers only, with per-container cooldowns and staggering
    const out = execSync("docker ps --format '{{.Names}}' ").toString();
    const lines = out.split('\n').map(l=>l.trim()).filter(Boolean);
    let restarted = 0;
    const cooldown = (cfg.containerRestartCooldownMinutes || 10) * 60;
    const maxPerRun = 10;
    for(const name of lines){
      if(!name.startsWith('blobevm_')) continue;
      try{
        const crestartPath = path.join(STATE_DIR, '.optimizer_restarts');
        try{ fs.mkdirSync(crestartPath, { recursive: true }); }catch(e){}
        const safeName = name.replace(/[^a-zA-Z0-9_.-]/g, '_');
        const p = path.join(crestartPath, safeName + '.last');
        let lastC = 0;
        try{ lastC = parseInt(fs.readFileSync(p,'utf8')) || 0; }catch(e){ lastC = 0; }
        if(now - lastC < cooldown){ log(`skip restart ${name} (cooldown)`); continue; }
        // perform restart
        execSync(`docker restart ${name}`);
        restarted++;
        log(`scheduler restart ${name}`);
        try{ fs.writeFileSync(p, String(Math.floor(now))); }catch(e){ log('write last restart per-container failed '+e); }
        // small stagger to avoid mass restarts
        try{ execSync('sleep 2'); }catch(e){}
        if(restarted >= maxPerRun) break;
      }catch(e){ log('scheduler restart failed '+name+' : '+e); }
    }
    if(restarted>0){ writeLastRestart(Math.floor(now)); log('performed scheduled restart of '+restarted+' containers'); }
  }catch(e){ log('performScheduledRestart error '+e); }
}

async function status(){
  const cfg = loadConfig();
  const stats = await gather();
  return { cfg, stats };
}

async function cli(){
  const cmd = process.argv[2];
  if(cmd === 'status'){
    const s = await status();
    console.log(JSON.stringify(s, null, 2));
    process.exit(0);
  }
  if(cmd === 'run-once'){
    const ev = await runOnce();
    console.log(JSON.stringify(ev, null, 2));
    process.exit(0);
  }
  if(cmd === 'set'){
    const key = process.argv[3];
    const val = process.argv[4];
    if(!key){ console.error('missing key'); process.exit(2); }
    const cfg = loadConfig();
    try{ cfg[key] = JSON.parse(val); }catch(e){ cfg[key]=val; }
    saveConfig(cfg);
    console.log('ok'); process.exit(0);
  }
  if(cmd === 'log-tail'){
    const p = path.join(LOG_DIR, 'optimizer.log');
    try{ const out = fs.readFileSync(p,'utf8'); console.log(out); }catch(e){ console.log(''); }
    process.exit(0);
  }
  console.log('OptimizerService: available commands: status | run-once | set <key> <json> | log-tail');
}

// Daemon mode: run guards on interval (every 15s) and enforce strict memory limits when enabled
async function daemonLoop(){
  log('daemon starting');
  const cfg = loadConfig();
  while(true){
    try{
      const cfgNow = loadConfig();
      if(cfgNow.enabled){
        await runOnce();
      }
      try{
        // perform scheduled restarts if scheduler enabled
        if(cfgNow.schedulerEnabled){
          performScheduledRestart(cfgNow);
        }
      }catch(e){ log('scheduler error '+e); }
    }catch(e){ log('daemon error '+e); }
    await new Promise(r=>setTimeout(r, 15000));
  }
}

if(require.main === module){
  const arg = process.argv[2];
  if(arg === 'daemon'){
    daemonLoop().catch(e=>{ log('daemon exit '+e); process.exit(1); });
  } else {
    cli();
  }
}
