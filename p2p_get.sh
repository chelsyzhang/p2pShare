python p2p_get.py \
  --signaling ws://114.132.234.219:2024 \
  --room myroom123 \
  --stun stun:stun.l.google.com:19302 \
  --turn turn:114.132.234.219:2025?transport=udp \
  --turn-user fileshareuser \
  --turn-pass filesharepass \
  --out /home/qi/code/data/downloaded_dataimages_0917.tar
