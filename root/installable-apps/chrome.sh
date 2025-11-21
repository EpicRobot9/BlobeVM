set -e
echo "**** install chrome ****"
export DEBIAN_FRONTEND=noninteractive
apt-get update || true
apt-get install -y --no-install-recommends wget gnupg ca-certificates curl || true
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/google-chrome.gpg ]]; then
	curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
fi
chmod a+r /etc/apt/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
apt-get update || true
# First attempt
if ! apt-get install -y google-chrome-stable; then
	echo "Retrying chrome install after fixing dependencies..."
	apt-get -f install -y || true
	apt-get install -y google-chrome-stable || {
		echo "Chrome installation still failing; attempting final fix-broken pass" >&2
		apt --fix-broken install -y || true
		apt-get install -y google-chrome-stable
	}
fi

rm -f ~/.config/google-chrome/SingletonLock
rm -f ~/.config/google-chrome/SingletonCookie
rm -f ~/.config/google-chrome/SingletonSocket


google-chrome-stable \
  --no-sandbox \
  --disable-gpu \
  --disable-software-rasterizer \
  --disable-dev-shm-usage


echo "Chrome install done."
