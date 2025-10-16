set -e
echo "**** install chrome ****"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y wget gnupg ca-certificates
install -m 0755 -d /etc/apt/keyrings
wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
chmod a+r /etc/apt/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" | tee /etc/apt/sources.list.d/google-chrome.list
apt-get update
apt-get install -y google-chrome-stable
