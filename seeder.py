# share.py
import argparse
import asyncio
import hashlib
import json
import os
import sys
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer
from aiortc import RTCConfiguration
from aiortc.contrib.signaling import BYE

CHUNK_SIZE = 64 * 1024  # 64KB，实践中 32~256KB 自行调优

def human(n):
    for unit in ["B","KB","MB","GB","TB"]:
        if n < 1024:
            return f"{n:.2f}{unit}"
        n/=1024
    return f"{n:.2f}PB"

async def run(args):
    # ICE 服务器：先尝试直连（host/srflx），失败走 relay
    ice_servers = []
    if args.stun:
        ice_servers.append(RTCIceServer(urls=[args.stun]))
    if args.turn and args.turn_user and args.turn_pass:
        ice_servers.append(RTCIceServer(urls=[args.turn],
                                        username=args.turn_user,
                                        credential=args.turn_pass))

    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    # 建数据通道（可靠、有序）
    channel = pc.createDataChannel("file", ordered=True)

    done_fut = asyncio.get_event_loop().create_future()
    file_size = os.path.getsize(args.file)
    file_name = os.path.basename(args.file)

    # 先发送元信息
    async def send_file():
        sha256 = hashlib.sha256()
        sent = 0
        start_ts = asyncio.get_event_loop().time()
        with open(args.file, "rb") as f:
            meta = {"kind":"meta","name":file_name,"size":file_size}
            channel.send(json.dumps(meta))
            await asyncio.sleep(0)  # 让 meta 先发出去

            while True:
                # 回压控制
                while channel.bufferedAmount > 8 * CHUNK_SIZE:
                    await asyncio.sleep(0.01)

                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
                channel.send(chunk)
                sent += len(chunk)

                # 简单进度
                if args.quiet is False:
                    pct = sent / file_size * 100 if file_size > 0 else 100
                    sys.stdout.write(f"\rSending {file_name}: {human(sent)}/{human(file_size)} ({pct:.1f}%)")
                    sys.stdout.flush()

            # 结束标记 + 校验
            digest = sha256.hexdigest()
            channel.send(json.dumps({"kind":"eof","sha256": digest}))
            if args.quiet is False:
                dt = asyncio.get_event_loop().time() - start_ts
                rate = sent / max(dt,1e-6)
                sys.stdout.write(f"\nDone in {dt:.2f}s, avg {human(rate)}/s, sha256={digest}\n")
                sys.stdout.flush()

    @channel.on("open")
    def on_open():
        asyncio.ensure_future(send_file())

    @channel.on("message")
    def on_message(msg):
        try:
            j = json.loads(msg) if isinstance(msg, (str, bytes)) and isinstance(msg, bytes) and msg[:1]==b'{' else json.loads(msg) if isinstance(msg, str) else None
        except Exception:
            j = None
        if isinstance(j, dict) and j.get("kind") == "ack":
            if not done_fut.done():
                done_fut.set_result(True)

    @pc.on("iceconnectionstatechange")
    def on_ice():
        print("ICE state:", pc.iceConnectionState)
        if pc.iceConnectionState in ("failed","disconnected","closed"):
            if not done_fut.done():
                done_fut.set_exception(RuntimeError(f"ICE {pc.iceConnectionState}"))

    # === 信令流程 ===
    async with websockets.connect(args.signaling) as ws:
        await ws.send(json.dumps({"type":"join","room":args.room,"role":"sender"}))
        joined = json.loads(await ws.recv())
        assert joined.get("type")=="joined"

        # 本地创建 offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await ws.send(json.dumps({"type":"sdp","data":{"sdp":pc.localDescription.sdp,"type":pc.localDescription.type}}))

        async def recv_task():
            async for raw in ws:
                m = json.loads(raw)
                if m["type"]=="sdp":
                    sdp = m["data"]
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"]))
                elif m["type"]=="ice":
                    cand = m["data"]
                    await pc.addIceCandidate(cand)
        rt = asyncio.create_task(recv_task())

        # 把本地 ICE 候选发给对端
        @pc.on("icecandidate")
        async def on_candidate(c):
            if c:
                await ws.send(json.dumps({"type":"ice","data":{
                    "candidate": c.to_sdp(),
                    "sdpMid": c.sdpMid,
                    "sdpMLineIndex": c.sdpMLineIndex
                }}))

        # 等待发送完成或失败
        await done_fut
        await ws.send(BYE)
        rt.cancel()
    await pc.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signaling", required=True, help="ws://your-vps-ip:8765")
    parser.add_argument("--room", required=True, help="room id (any string)")
    parser.add_argument("--file", required=True, help="path of file to send")
    parser.add_argument("--stun", default="stun:stun.l.google.com:19302", help="STUN url")
    parser.add_argument("--turn", help="TURN url, e.g. turn:your-vps:3478?transport=udp")
    parser.add_argument("--turn-user", help="TURN username")
    parser.add_argument("--turn-pass", help="TURN password")
    parser.add_argument("--quiet", action="store_true", default=False)
    args = parser.parse_args()
    asyncio.run(run(args))
