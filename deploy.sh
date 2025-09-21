#!/usr/bin/env bash
set -e

WITH_TURN=false
for arg in "$@"; do
    if [ "$arg" == "--with-turn" ]; then
        WITH_TURN=true
    fi
done

echo "[*] Updating system..."
sudo apt update -y
sudo apt install -y python3 python3-venv python3-pip git curl ufw

echo "[*] Creating working dir..."
sudo mkdir -p /opt/p2p
sudo chown $USER:$USER /opt/p2p
cd /opt/p2p

# -------------------------------
# 信令服务器
# -------------------------------
echo "[*] Installing signaling server..."
cat > signaling_server.py <<'PYCODE'
import asyncio, json
from aiohttp import web, WSMsgType

ROOMS = {}  # room_id -> set(websockets)

async def index(request):
    return web.Response(text="Simple signaling server", content_type="text/plain")

async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    room = None
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            typ = data.get("type")
            if typ == "join":
                room = data.get("room")
                ROOMS.setdefault(room, set()).add(ws)
                await ws.send_json({"type":"joined","room":room})
            else:
                if room and room in ROOMS:
                    for peer in list(ROOMS[room]):
                        if peer is not ws:
                            try:
                                await peer.send_json(data)
                            except:
                                ROOMS[room].discard(peer)
        elif msg.type == WSMsgType.ERROR:
            break
    if room and ws in ROOMS.get(room, set()):
        ROOMS[room].discard(ws)
    return ws

app = web.Application()
app.add_routes([web.get("/", index), web.get("/ws", ws_handler)])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=2024)
PYCODE

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate
pip install aiohttp

# -------------------------------
# systemd 服务：信令
# -------------------------------
echo "[*] Creating systemd service for signaling..."
cat | sudo tee /etc/systemd/system/p2p-signaling.service > /dev/null <<'SERVICE'
[Unit]
Description=P2P Signaling Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/p2p
ExecStart=/opt/p2p/venv/bin/python3 signaling_server.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable p2p-signaling
sudo systemctl restart p2p-signaling

# -------------------------------
# coturn (可选)
# -------------------------------
if $WITH_TURN; then
    echo "[*] Installing coturn..."
    sudo apt install -y coturn
    sudo sed -i 's/#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn

    cat | sudo tee /etc/turnserver.conf > /dev/null <<'CONF'
listening-port=2025
fingerprint
lt-cred-mech
realm=p2p.local
user=turnuser:StrongTurnPass123
min-port=2026
max-port=2125
verbose
CONF

    sudo systemctl enable coturn
    sudo systemctl restart coturn
fi

# -------------------------------
# 防火墙
# -------------------------------
echo "[*] Configuring firewall..."
sudo ufw allow 2024/tcp # signaling
if $WITH_TURN; then
    sudo ufw allow 2025/tcp
    sudo ufw allow 2025/udp
    sudo ufw allow 2026:2125/udp
    sudo ufw allow 2026:2125/tcp
fi
sudo ufw --force enable

echo "[*] Done."
echo "Signaling server running at ws://114.132.234.219:2024/ws"
if $WITH_TURN; then
    echo "TURN server running at turn:114.132.234.219:2025"
    echo "  username: turnuser"
    echo "  password: StrongTurnPass123"
fi
