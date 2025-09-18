# server.py - 极简 WebSocket 信令服务器
# 用途：按 file_id 把 seeder 与 client 配对，转发 offer/answer/ice-candidate


import asyncio
import json
import logging
import argparse
import websockets
from typing import Dict, Optional


logging.basicConfig(level=logging.INFO)


# 每个 room(file_id) 保持两个角色的连接
class Room:
    def __init__(self):
        self.seeder: Optional[websockets.WebSocketServerProtocol] = None
        self.client: Optional[websockets.WebSocketServerProtocol] = None


rooms: Dict[str, Room] = {}


async def handler(ws, path):
    file_id = None
    role = None
    try:
        async for msg in ws:
            data = json.loads(msg)
            t = data.get("type")

            if t == "register":
                role = data.get("role")
                file_id = data.get("file_id")
                if not file_id or role not in ("seeder", "client"):
                    await ws.send(json.dumps({"type": "error", "reason": "bad register"}))
                    continue
                room = rooms.setdefault(file_id, Room())
                if role == "seeder":
                    room.seeder = ws
                else:
                    room.client = ws
                logging.info("%s joined file_id=%s", role, file_id)
                await ws.send(json.dumps({"type": "registered", "file_id": file_id, "role": role}))


                # 如果两端都在，通知彼此就绪
                if room.seeder and room.client:
                    try:
                        await room.client.send(json.dumps({"type": "peer-ready", "file_id": file_id}))
                        await room.seeder.send(json.dumps({"type": "peer-ready", "file_id": file_id}))
                    except Exception:
                        logging.exception("notify peer-ready failed")


                elif t in ("offer", "answer", "candidate"):
                    file_id = data.get("file_id")
                    room = rooms.get(file_id)
                    if not room:
                        await ws.send(json.dumps({"type": "error", "reason": "unknown file_id"}))
                        continue
                    dst = None
                    if t == "offer":
                        # client -> seeder
                        dst = room.seeder
                    elif t == "answer":
                        # seeder -> client
                        dst = room.client
                    elif t == "candidate":
                        # 双向候选转发
                        # 判断发送者是谁，转发给对端
                        if ws is room.seeder:
                            dst = room.client
                        else:
                            dst = room.seeder
                    if dst:
                        try:
                            await dst.send(json.dumps(data))
                        except Exception:
                            logging.exception("forward failed: %s", t)
    except websockets.ConnectionClosed:
        pass
    finally:
        # 清理
        if file_id and role:
            room = rooms.get(file_id)
            if room:
                if role == "seeder" and room.seeder is ws:
                    room.seeder = None
                if role == "client" and room.client is ws:
                    room.client = None
                if not room.seeder and not room.client:
                    rooms.pop(file_id, None)
        logging.info("connection closed: role=%s file_id=%s", role, file_id)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()


    async with websockets.serve(handler, args.host, args.port, max_size=2**25):
        logging.info("signaling on ws://%s:%d", args.host, args.port)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())