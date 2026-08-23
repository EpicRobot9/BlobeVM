import json, sys
sys.path.insert(0, "/app")
from remote_hosts import ConfiguredVmHostRegistry
r = ConfiguredVmHostRegistry(path="/opt/blobe-vm/remote-hosts.json")
h = r.get("epic-pc")
caps = h.client.capabilities()
if isinstance(caps, dict):
    out = {}
    for k, v in caps.items():
        if k in ("capabilities","resources","blockers","gates","checks","missing","warnings","version","agentVersion"):
            if k == "capabilities" and isinstance(v, dict):
                out[k] = {str(a): bool(b) if isinstance(b,bool) else str(b)[:120] for a,b in v.items()}
            elif k in ("resources","blockers","gates","checks","missing","warnings") and isinstance(v, (dict,list)):
                out[k] = v
            elif k in ("version","agentVersion"):
                out[k] = str(v)[:120]
    print(json.dumps(out, sort_keys=True))
else:
    print(json.dumps({"type":type(caps).__name__}))
