#!/usr/bin/env bash
set -e
echo "**** install spotify ****"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl gnupg ca-certificates
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.spotify.com/debian/pubkey_5E3C45D7B312C643.gpg | gpg --dearmor -o /etc/apt/keyrings/spotify.gpg
chmod a+r /etc/apt/keyrings/spotify.gpg
echo "deb [signed-by=/etc/apt/keyrings/spotify.gpg] http://repository.spotify.com stable non-free" | tee /etc/apt/sources.list.d/spotify.list
apt-get update
apt-get install -y spotify-client
