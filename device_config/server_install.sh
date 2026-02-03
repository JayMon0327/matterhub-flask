#!bin/bash

mkdir matterhub
cd matterhub
git init
git remote add origin https://github.com/nano-2-ly/whatsmatter-hub-flask-server.git
git pull origin master
sudo apt install python3-pip
pip install -r requirements.txt --break-system-packages
sudo cp device_config/matterhub.service /etc/systemd/system
systemctl enable matterhub.service
ngrok config add-authtoken 2ZX2X31qCQXCbNiMVDsv1qtG7Vz_2toFAYXpRQyt7rM2hURvg
sudo sysctl -w net.ipv6.conf.all.disable_ipv6=0
sudo sysctl -w net.ipv6.conf.default.disable_ipv6=0
sudo sysctl -w net.ipv6.conf.lo.disable_ipv6=0
sudo cp docker-compose.yml /usr/share/hassio/homeassistant
