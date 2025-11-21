#!/usr/bin/env bash
set -euo pipefail
echo "**** install spotify ****"
export DEBIAN_FRONTEND=noninteractive

# If spotify is already installed, exit early
if command -v spotify >/dev/null 2>&1 || dpkg -s spotify-client >/dev/null 2>&1; then
	echo "spotify-client already installed; skipping"
	exit 0
fi

# Ensure basic tooling
apt-get update
apt-get install -y --no-install-recommends curl gnupg ca-certificates dirmngr || apt-get install -y curl gnupg ca-certificates || true

# Prepare keyrings directory
install -m 0755 -d /etc/apt/keyrings

# Fetch Spotify GPG key and install into keyrings. Prefer gpg --dearmor, fallback to apt-key add if necessary.
if command -v gpg >/dev/null 2>&1; then
	curl -fsSL https://download.spotify.com/debian/pubkey_5E3C45D7B312C643.gpg | gpg --dearmor -o /etc/apt/keyrings/spotify.gpg
	chmod a+r /etc/apt/keyrings/spotify.gpg
else
	# Older systems: use apt-key as fallback (deprecated but sometimes available)
	curl -fsSL https://download.spotify.com/debian/pubkey_5E3C45D7B312C643.gpg | apt-key add - || true
fi

# Add APT source (use signed-by when keyring is present)
if [[ -f /etc/apt/keyrings/spotify.gpg ]]; then
	echo "deb [signed-by=/etc/apt/keyrings/spotify.gpg] http://repository.spotify.com stable non-free" | tee /etc/apt/sources.list.d/spotify.list
else
	echo "deb http://repository.spotify.com stable non-free" | tee /etc/apt/sources.list.d/spotify.list
fi

# Try to fetch additional Spotify signing keys from keyservers (some repo metadata
# is signed by secondary keys). Append any found keys into the spotify keyring.
if command -v gpg >/dev/null 2>&1; then
	for _k in E1096BCBFF6D418796DE78515384CE82BA52C83A B420FD3777CCE3A7F0076B55C85668DF69375001; do
		set +e
		gpg --keyserver hkps://keyserver.ubuntu.com --recv-keys "${_k}" >/dev/null 2>&1 || true
		if gpg --list-keys "${_k}" >/dev/null 2>&1; then
			gpg --export --armor "${_k}" | gpg --dearmor >> /etc/apt/keyrings/spotify.gpg 2>/dev/null || true
			chmod a+r /etc/apt/keyrings/spotify.gpg || true
		fi
		set -euo pipefail
	done
fi

apt-get update
apt-get install -y --no-install-recommends spotify-client || {
	echo "spotify-client install failed; attempting to fix by updating apt and retrying..."
	apt-get update
	apt-get install -y spotify-client || { echo "spotify install failed"; exit 1; }
}

echo "**** spotify install complete ****"
