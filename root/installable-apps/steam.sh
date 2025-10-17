set -e
echo "**** install steam ****"
export DEBIAN_FRONTEND=noninteractive
apt-get update || true
# ensure required deps for the launcher
apt-get install -y --no-install-recommends ca-certificates wget lsof zenity || true
# purge any previous steam bits to avoid duplicates or broken state
apt-get -y purge steam steam-launcher || true
rm -f /tmp/steam.deb || true
wget "https://steamcdn-a.akamaihd.net/client/installer/steam.deb" -O /tmp/steam.deb
dpkg -i /tmp/steam.deb || apt-get -f install -y
dpkg -i /tmp/steam.deb || true
rm -f /tmp/steam.deb
apt-get -y autoremove || true
apt-get -y autoclean || true
