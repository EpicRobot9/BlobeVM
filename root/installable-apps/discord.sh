set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update || true
apt-get install -y --no-install-recommends ca-certificates wget gnupg libatomic1 || true
# Remove any prior discord package to avoid duplicates
apt-get -y purge discord || true
rm -f /tmp/discord.deb || true
wget -O /tmp/discord.deb "https://discord.com/api/download?platform=linux&format=deb"
dpkg -i /tmp/discord.deb || apt-get -f install -y
dpkg -i /tmp/discord.deb || true
rm -f /tmp/discord.deb
apt-get -y autoremove || true
apt-get -y autoclean || true