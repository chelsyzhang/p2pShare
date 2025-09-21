import asyncio
import json
import websockets
from collections import defaultdict, deque

# 房间结构：存 sender/receiver websocket，以及消息缓存队列
rooms = defaultdict(lambda: {
    "sender": None,
    "receiver": None,
    "queue": deque(maxlen=100)  # 缓存最近 100 条消息（SDP/ICE）
})
LOCK = asyncio.Lock()


async def handler(ws):
    room_name = None
    role = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send(json.dumps({"type": "error", "error": "invalid_json"}))
                continue

            t = msg.get("type")

            if t == "join":
                # {type:"join", room:"abc", role:"sender"/"receiver"}
                room_name = msg.get("room")
                role = msg.get("role")
                if not room_name or role not in ("sender", "receiver"):
                    await ws.send(json.dumps({"type": "error", "error": "bad_join"}))
                    continue

                async with LOCK:
                    r = rooms[room_name]
                    # 如果已有同角色，挤掉旧的
                    old = r.get(role)
                    if old and old.open:
                        try:
                            await old.close()
                        except:
                            pass
                    r[role] = ws
                    # 回确认
                    await ws.send(json.dumps({
                        "type": "joined",
                        "room": room_name,
                        "role": role
                    }))
                    # 回放缓存消息（只给新加入的另一方发过的消息）
                    for who, m in list(r["queue"]):
                        if who != role:
                            await ws.send(m)

            elif t in ("sdp", "ice"):
                if not room_name or not role:
                    await ws.send(json.dumps({"type": "error", "error": "join_first"}))
                    continue

                other = "receiver" if role == "sender" else "sender"
                msg_str = json.dumps(msg)

                async with LOCK:
                    r = rooms[room_name]
                    peer = r.get(other)
                    if peer and peer.open:
                        await peer.send(msg_str)
                    else:
                        # 对方还没来，先缓存
                        r["queue"].append((role, msg_str))

            elif t == "leave":
                break

            else:
                await ws.send(json.dumps({"type": "error", "error": "unknown_type"}))

    except websockets.ConnectionClosed:
        pass
    finally:
        if room_name and role:
            async with LOCK:
                r = rooms.get(room_name)
                if r and r.get(role) is ws:
                    r[role] = None
                if r and not r["sender"] and not r["receiver"]:
                    del rooms[room_name]


async def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()

    async with websockets.serve(handler, args.host, args.port, max_size=2**22):
        print(f"Signal server listening on ws://{args.host}:{args.port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
