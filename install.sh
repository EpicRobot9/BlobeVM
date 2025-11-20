git clone https://github.com/EpicRobot9/BlobeVM
cd BlobeVM
pip install textual
sleep 2
python3 installer.py
docker build -t blobevm . --no-cache
cd ..

sudo apt update
sudo apt install -y jq

mkdir Save
cp -r BlobeVM/root/config/* Save

json_file="BlobeVM/options.json"
if jq ".enablekvm" "$json_file" | grep -q true; then
    docker run -d --name=BlobeVM -e PUID=1000 -e PGID=1000 --device=/dev/kvm --security-opt seccomp=unconfined -e TZ=Etc/UTC -e SUBFOLDER=/ -e TITLE=BlobeVM -p 3000:3000 --shm-size="2gb" -v $(pwd)/Save:/config --restart unless-stopped blobevm
else
    docker run -d --name=BlobeVM -e PUID=1000 -e PGID=1000 --security-opt seccomp=unconfined -e TZ=Etc/UTC -e SUBFOLDER=/ -e TITLE=BlobeVM -p 3000:3000 --shm-size="2gb" -v $(pwd)/Save:/config --restart unless-stopped blobevm
fi
clear
echo "BLOBEVM WAS INSTALLED SUCCESSFULLY! Check Port Tab"

# --- Install and enable Blobe Optimizer service so it runs immediately ---
echo "Installing Blobe Optimizer service..."

sudo mkdir -p /opt/blobe-vm
# Prefer copying from the cloned `BlobeVM` folder if present (avoid nested copies)
if [[ -d "$PWD/BlobeVM" ]]; then
    echo "Copying repository from $PWD/BlobeVM to /opt/blobe-vm"
    sudo rsync -a "$PWD/BlobeVM/" /opt/blobe-vm/
else
    echo "Copying current directory contents to /opt/blobe-vm"
    sudo rsync -a "$PWD"/ /opt/blobe-vm/
fi

# If we accidentally copied a nested `BlobeVM` directory (e.g., /opt/blobe-vm/BlobeVM/optimizer),
# flatten it so optimizer lives at /opt/blobe-vm/optimizer as expected by the dashboard.
if [[ -d /opt/blobe-vm/BlobeVM && ! -d /opt/blobe-vm/optimizer ]]; then
    echo "Detected nested BlobeVM folder; flattening contents into /opt/blobe-vm"
    sudo rsync -a /opt/blobe-vm/BlobeVM/ /opt/blobe-vm/
    sudo rm -rf /opt/blobe-vm/BlobeVM
fi

# Ensure Node.js (Node 18 LTS) is present via NodeSource for a modern runtime
if ! command -v node >/dev/null 2>&1; then
    echo "Installing Node.js 18.x via NodeSource"
    sudo apt-get update -y
    sudo apt-get install -y curl ca-certificates gnupg lsb-release
    curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "Node already installed: $(node --version)"
fi

# Make optimizer script and ensure script executable and create log dir
sudo mkdir -p /var/blobe/logs/optimizer
sudo chmod -R 755 /opt/blobe-vm/optimizer || true
sudo chmod +x /opt/blobe-vm/optimizer/OptimizerService.js 2>/dev/null || true
sudo chmod +x /opt/blobe-vm/optimizer/optimizer-ensure.sh 2>/dev/null || true

# Install systemd service file and enable/start it
if [[ -f "blobe-optimizer.service" ]]; then
    sudo cp blobe-optimizer.service /etc/systemd/system/blobe-optimizer.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now blobe-optimizer.service || sudo systemctl start blobe-optimizer.service || true
    echo "Blobe Optimizer service installed and started (if supported on this system)."
else
    echo "blobe-optimizer.service not found in repo; skipping service install."
fi
