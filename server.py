# server.py - WebSocket 信令服务器（支持双向发起 offer / answer / candidate 转发）
# 功能：按 file_id 把两端配对，并且不假设由哪一端先发起 offer。

import asyncio
import json
import logging
import argparse
import websockets
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO)

class Room:
    def __init__(self):
        self.a: Optional[websockets.WebSocketServerProtocol] = None
        self.b: Optional[websockets.WebSocketServerProtocol] = None

    def add(self, role: str, ws: websockets.WebSocketServerProtocol):
        # 兼容旧的 role 名称："seeder"/"client" 或任意字符串
        if self.a is None:
            self.a = ws
            return "a"
        elif self.b is None and ws is not self.a:
            self.b = ws
            return "b"
        # 已满或重复
        return None

    def counterpart(self, ws):
        if ws is self.a:
            return self.b
        if ws is self.b:
            return self.a
        return None

rooms: Dict[str, Room] = {}

async def handler(ws, path):
    file_id = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            t = msg.get("type")
            if t == "register":
                file_id = msg.get("file_id")
                role = msg.get("role", "peer")
                if not file_id:
                    await ws.send(json.dumps({"type": "error", "reason": "missing file_id"}))
                    continue
                room = rooms.setdefault(file_id, Room())
                slot = room.add(role, ws)
                if not slot:
                    await ws.send(json.dumps({"type": "error", "reason": "room full or duplicate"}))
                    continue
                logging.info("peer joined file_id=%s as %s (role=%s)", file_id, slot, role)
                await ws.send(json.dumps({"type": "registered", "file_id": file_id, "slot": slot}))
                # 两端就绪后互相通知
                if room.a and room.b:
                    for peer in (room.a, room.b):
                        try:
                            await peer.send(json.dumps({"type": "peer-ready", "file_id": file_id}))
                        except Exception:
                            logging.exception("notify peer-ready failed")
                continue

            # 以下所有信令都无方向假设：直接转发给对端
            if t in ("offer", "answer", "candidate", "bye"):
                if not file_id:
                    file_id = msg.get("file_id")
                room = rooms.get(file_id)
                if not room:
                    await ws.send(json.dumps({"type": "error", "reason": "unknown file_id"}))
                    continue
                dst = room.counterpart(ws)
                if not dst:
                    await ws.send(json.dumps({"type": "error", "reason": "peer not ready"}))
                    continue
                try:
                    await dst.send(json.dumps(msg))
                except Exception:
                    logging.exception("forward %s failed", t)
                continue

    except websockets.ConnectionClosed:
        pass
    finally:
        if file_id:
            room = rooms.get(file_id)
            if room:
                if room.a is ws:
                    room.a = None
                if room.b is ws:
                    room.b = None
                if not room.a and not room.b:
                    rooms.pop(file_id, None)
        logging.info("connection closed for file_id=%s", file_id)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    async with websockets.serve(handler, args.host, args.port, max_size=2**25):
        logging.info("signaling on ws://%s:%d", args.host, args.port)
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())