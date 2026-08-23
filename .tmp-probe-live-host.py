import json, sys
sys.path.insert(0, "/app")
from remote_hosts import ConfiguredVmHostRegistry
registry = ConfiguredVmHostRegistry(path="/opt/blobe-vm/remote-hosts.json")
for record in registry.public_records():
    safe = {k: record.get(k) for k in ("id","display_name","kind","platform","provider","transport","online","capabilities","resources")}
    print(json.dumps(safe, sort_keys=True))
