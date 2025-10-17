set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update || true
apt-get install -y --no-install-recommends wget ca-certificates || true
# ensure JRE and gdk deps present
apt-get install -y default-jre libgdk-pixbuf-2.0-0 || true
# purge old package to avoid duplicates
apt-get -y purge minecraft-launcher || true
rm -f /tmp/Minecraft.deb || true
wget -O /tmp/Minecraft.deb https://launcher.mojang.com/download/Minecraft.deb
dpkg -i /tmp/Minecraft.deb || apt-get -f install -y
dpkg -i /tmp/Minecraft.deb || true
rm -f /tmp/Minecraft.deb
apt-get -y autoremove || true
apt-get -y autoclean || true
