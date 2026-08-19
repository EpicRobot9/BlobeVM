FROM ghcr.io/linuxserver/baseimage-kasmvnc:ubuntujammy

# set version label
ARG BUILD_DATE
ARG VERSION
LABEL build_version="[Mollomm1 Mod] Linuxserver.io version:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL maintainer="mollomm1"

ARG DEBIAN_FRONTEND="noninteractive"

# prevent Ubuntu's firefox stub from being installed
COPY /root/etc/apt/preferences.d/firefox-no-snap /etc/apt/preferences.d/firefox-no-snap

COPY options.json /

COPY /root/ /

RUN \
  # The upstream base image may retain an EOL NodeSource entry; remove it so apt update remains buildable. \
  rm -f /etc/apt/sources.list.d/*nodesource* || true && \
  echo "**** install packages ****" && \
  add-apt-repository -y ppa:mozillateam/ppa && \
  apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y firefox jq wget && \
  chmod +x /install-de.sh && \
  /install-de.sh

RUN \
  chmod +x /installapps.sh && \
  /installapps.sh && \
  rm /installapps.sh

RUN \
  # Fix Kasm path-mode websocket URL under subfolder routing:
  # use an absolute subfolder path (/vm/<name>/websockify), not relative vm/<name>/websockify
  perl -0777 -i -pe "s/PATH = '&path=' \+ SUBFOLDER\.substring\(1\) \+ 'websockify'/PATH = '&path=' + SUBFOLDER + 'websockify'/g" /kclient/index.js && \
  # KasmVNC websocket endpoint is served at '/' on port 6901 in current base image;
  # keep external URL at /websockify but strip that prefix at nginx proxy_pass.
  sed -i 's#proxy_pass               http://127.0.0.1:6901;#proxy_pass               http://127.0.0.1:6901/;#g' /etc/nginx/sites-available/default && \
  echo "**** cleanup ****" && \
  apt-get autoclean && \
  rm -rf \
    /config/.cache \
    /var/lib/apt/lists/* \
    /var/tmp/* \
    /tmp/*

# Copy wrapper into image and update desktop file so launches use wrapper
COPY root/usr-local/google-chrome-wrapper /usr/local/bin/google-chrome-wrapper
RUN \
  chmod 0755 /usr/local/bin/google-chrome-wrapper && \
  sed -i 's|Exec=/usr/bin/google-chrome-stable .*%U|Exec=/usr/local/bin/google-chrome-wrapper %U|' /usr/share/applications/google-chrome.desktop || true && \
  sed -i 's|Exec=/usr/bin/google-chrome-stable$|Exec=/usr/local/bin/google-chrome-wrapper|' /usr/share/applications/google-chrome.desktop || true && \
  sed -i 's|Exec=/usr/bin/google-chrome-stable --incognito|Exec=/usr/local/bin/google-chrome-wrapper --incognito|' /usr/share/applications/google-chrome.desktop || true
  
# ports and volumes
EXPOSE 3000
VOLUME /config
