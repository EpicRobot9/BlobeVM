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
# Copy the cloned repo into /opt/blobe-vm so dashboard/service can access it.
# We expect this script to be run from the directory that contains the cloned `BlobeVM` folder
# (the top-level clone step earlier creates `BlobeVM`). If run elsewhere, try to copy any BlobeVM folder we find.
REPO_SRC=""
if [[ -d "$PWD/BlobeVM" ]]; then
    REPO_SRC="$PWD/BlobeVM"
elif [[ -d "$PWD" && -f "$PWD/installer.py" && -d "$PWD/optimizer" ]]; then
    # Running from repo root already
    REPO_SRC="$PWD"
else
    # Try to locate a nearby clone
    FOUND=$(find "$PWD" -maxdepth 2 -type d -name BlobeVM 2>/dev/null | head -n1 || true)
    if [[ -n "$FOUND" ]]; then
        REPO_SRC="$FOUND"
    fi
fi

if [[ -n "$REPO_SRC" ]]; then
    echo "Copying repository from $REPO_SRC to /opt/blobe-vm"
    sudo rsync -a "$REPO_SRC/" /opt/blobe-vm/
else
    echo "No local BlobeVM repo found in working dir; attempting to copy current dir contents"
    sudo rsync -a "$PWD"/ /opt/blobe-vm/ || true
fi

# Flatten nested copy if necessary (ensure /opt/blobe-vm/optimizer exists)
if [[ ! -d /opt/blobe-vm/optimizer && -d /opt/blobe-vm/BlobeVM ]]; then
    echo "Detected nested /opt/blobe-vm/BlobeVM; flattening into /opt/blobe-vm"
    sudo rsync -a /opt/blobe-vm/BlobeVM/ /opt/blobe-vm/
    sudo rm -rf /opt/blobe-vm/BlobeVM
fi

# Also handle cases where the repo content was copied under /opt/blobe-vm/repo
# (some installer runs create a `repo/` directory). Flatten it too if present.
if [[ ! -d /opt/blobe-vm/optimizer && -d /opt/blobe-vm/repo ]]; then
    echo "Detected nested /opt/blobe-vm/repo; flattening into /opt/blobe-vm"
    sudo rsync -a /opt/blobe-vm/repo/ /opt/blobe-vm/
    sudo rm -rf /opt/blobe-vm/repo
fi

# If optimizer files are present in repo but not in /opt/blobe-vm, copy them explicitly
if [[ (! -d /opt/blobe-vm/optimizer) ]]; then
    # If we have a detected repo source, copy from it. Otherwise try cloning from GitHub.
    if [[ -n "$REPO_SRC" && -d "$REPO_SRC/optimizer" ]]; then
        echo "Copying optimizer/ from repo into /opt/blobe-vm/optimizer"
        sudo mkdir -p /opt/blobe-vm/optimizer
        sudo rsync -a "$REPO_SRC/optimizer/" /opt/blobe-vm/optimizer/
    else
        echo "Optimizer not found locally; cloning repo from GitHub to retrieve optimizer"
        TMP_CLONE="/tmp/BlobeVM_installer_$$"
        rm -rf "$TMP_CLONE"
        git clone --depth 1 https://github.com/EpicRobot9/BlobeVM "$TMP_CLONE" || true
        if [[ -d "$TMP_CLONE/optimizer" ]]; then
            sudo mkdir -p /opt/blobe-vm/optimizer
            sudo rsync -a "$TMP_CLONE/optimizer/" /opt/blobe-vm/optimizer/
        else
            echo "Failed to obtain optimizer from remote repo."
        fi
        rm -rf "$TMP_CLONE"
    fi
fi

# Ensure log dir exists
sudo mkdir -p /var/blobe/logs/optimizer
sudo chown root:root /var/blobe/logs/optimizer || true
sudo chmod 755 /var/blobe/logs/optimizer || true

# Ensure python3-pip and psutil are installed for server stats
if ! command -v pip3 >/dev/null 2>&1; then
    echo "pip3 not found — installing python3-pip"
    sudo apt-get update -y
    sudo apt-get install -y python3-pip
fi
if ! python3 -c "import psutil" >/dev/null 2>&1; then
    echo "Installing python psutil"
    sudo pip3 install psutil || true
else
    echo "psutil already available"
fi

# Ensure Docker CLI is available (needed for vm stats/logs/exec endpoints)
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker CLI not found — installing docker.io"
    sudo apt-get update -y
    sudo apt-get install -y docker.io || true
else
    echo "Docker CLI present: $(docker --version)"
fi

# Install Node.js 18.x via NodeSource if missing (modern LTS)
if ! command -v node >/dev/null 2>&1; then
    echo "Node.js not found — installing Node.js 18.x via NodeSource"
    sudo apt-get update -y
    sudo apt-get install -y curl ca-certificates gnupg lsb-release
    curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "Node already installed: $(node --version)"
fi

# Ensure optimizer scripts are executable
if [[ -d /opt/blobe-vm/optimizer ]]; then
    sudo chmod -R 755 /opt/blobe-vm/optimizer || true
    sudo chmod +x /opt/blobe-vm/optimizer/OptimizerService.js 2>/dev/null || true
    sudo chmod +x /opt/blobe-vm/optimizer/optimizer-ensure.sh 2>/dev/null || true
    # If package.json present, install deps
    if [[ -f /opt/blobe-vm/optimizer/package.json ]]; then
        echo "Installing npm dependencies for optimizer"
        (cd /opt/blobe-vm/optimizer && sudo npm ci --no-audit --no-fund) || (cd /opt/blobe-vm/optimizer && sudo npm install --no-audit --no-fund) || true
    fi
fi

# Install systemd service file and enable/start it
if [[ -f "/opt/blobe-vm/blobe-optimizer.service" ]]; then
    sudo cp /opt/blobe-vm/blobe-optimizer.service /etc/systemd/system/blobe-optimizer.service
elif [[ -f "blobe-optimizer.service" ]]; then
    sudo cp blobe-optimizer.service /etc/systemd/system/blobe-optimizer.service
fi

if [[ -f "/etc/systemd/system/blobe-optimizer.service" ]]; then
    sudo systemctl daemon-reload
    sudo systemctl enable --now blobe-optimizer.service || sudo systemctl start blobe-optimizer.service || true
    echo "Blobe Optimizer service installed and started (if supported on this system)."
else
    echo "blobe-optimizer.service not found in repo; skipping service install."
fi

# Restart or reload dashboard so it sees copied files (only if systemd unit exists)
if systemctl list-units --full -all | grep -q '^blobedash.service'; then
    echo "Restarting blobedash.service to pick up new files"
    sudo systemctl restart blobedash.service || true
fi

# Build dashboard_v2 frontend so it's available at /Dashboard on first run
if [[ -d /opt/blobe-vm/dashboard_v2 ]]; then
    echo "Building dashboard_v2 frontend"
    DASH_DIR="/opt/blobe-vm/dashboard_v2"
    LAST_ERR="$DASH_DIR/last_error.txt"
    # remove previous error
    sudo rm -f "$LAST_ERR" 2>/dev/null || true
    if [[ -f "$DASH_DIR/package.json" ]]; then
        (cd "$DASH_DIR" && sudo npm ci --unsafe-perm=true --no-audit --no-fund) 2>"$LAST_ERR" || true
        # run build; capture stderr to last_error.txt for troubleshooting
        if (cd "$DASH_DIR" && sudo npm run build --if-present) 2>>"$LAST_ERR"; then
            echo "dashboard_v2 built successfully"
            sudo rm -f "$LAST_ERR" 2>/dev/null || true
        else
            echo "dashboard_v2 build failed — see $LAST_ERR for details"
            sudo chown $(whoami):$(whoami) "$LAST_ERR" || true
        fi
    else
        echo "No dashboard_v2/package.json found; skipping build"
    fi
fi
