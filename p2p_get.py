# fetch.py
import argparse
import asyncio
import hashlib
import json
import os
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration

CHUNK_LIMIT_FOR_FLUSH = 4 * 1024 * 1024  # 每 4MB flush 一次

async def run(args):
    ice_servers = []
    if args.stun:
        ice_servers.append(RTCIceServer(urls=[args.stun]))
    if args.turn and args.turn_user and args.turn_pass:
        ice_servers.append(RTCIceServer(urls=[args.turn], username=args.turn_user, credential=args.turn_pass))

    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    file = None
    sha256 = hashlib.sha256()
    expect_size = None
    recv_size = 0
    out_path = None
    buffered = 0
    channel_ready = asyncio.Event()
    sender_channel = None

    @pc.on("datachannel")
    def on_datachannel(ch):
        nonlocal sender_channel
        sender_channel = ch
        @ch.on("open")
        def _open():
            channel_ready.set()

        @ch.on("message")
        def _msg(msg):
            nonlocal file, expect_size, recv_size, out_path, buffered
            if isinstance(msg, bytes):
                if file is None:
                    # 未收到 meta 就来了二进制，忽略
                    return
                file.write(msg)
                sha256.update(msg)
                recv_size += len(msg)
                buffered += len(msg)
                # 简易 flush 控制
                if buffered >= CHUNK_LIMIT_FOR_FLUSH:
                    file.flush()
                    os.fsync(file.fileno())
                    buffered = 0
                if args.quiet is False and expect_size:
                    pct = recv_size / expect_size * 100
                    print(f"\rReceiving {os.path.basename(out_path)}: {recv_size}/{expect_size} ({pct:.1f}%)", end="")
            else:
                # 文本：meta/eof
                try:
                    j = json.loads(msg)
                except Exception:
                    return
                if j.get("kind") == "meta":
                    name = j["name"]
                    expect_size = int(j["size"])
                    out_name = args.output if args.output else name
                    out_path = os.path.abspath(out_name)
                    # 防覆盖
                    if os.path.exists(out_path):
                        if args.overwrite:
                            pass
                        else:
                            base, ext = os.path.splitext(out_path)
                            k = 1
                            while os.path.exists(out_path):
                                out_path = f"{base}.recv{'' if k==1 else k}{ext}"
                                k += 1
                    file = open(out_path, "wb")
                    print(f"Receiving to: {out_path}")
                elif j.get("kind") == "eof":
                    remote_digest = j["sha256"]
                    if file:
                        file.flush()
                        os.fsync(file.fileno())
                        file.close()
                        file = None
                    local_digest = sha256.hexdigest()
                    print()
                    print(f"Done. sha256 local={local_digest}")
                    if remote_digest != local_digest:
                        print(f"WARNING: sha256 mismatch! remote={remote_digest}")
                    # 回 ACK
                    ch.send(json.dumps({"kind":"ack"}))

    @pc.on("iceconnectionstatechange")
    def on_ice():
        print("ICE state:", pc.iceConnectionState)

    async with websockets.connect(args.signaling) as ws:
        await ws.send(json.dumps({"type":"join","room":args.room,"role":"receiver"}))
        joined = json.loads(await ws.recv())
        assert joined.get("type")=="joined"

        # 等待对端 offer，设置远端，再应答
        async def recv_task():
            async for raw in ws:
                m = json.loads(raw)
                if m["type"]=="sdp":
                    sdp = m["data"]
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"]))
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    await ws.send(json.dumps({"type":"sdp","data":{
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type
                    }}))
                elif m["type"]=="ice":
                    cand = m["data"]
                    await pc.addIceCandidate(cand)
        rt = asyncio.create_task(recv_task())

        @pc.on("icecandidate")
        async def on_candidate(c):
            if c:
                await ws.send(json.dumps({"type":"ice","data":{
                    "candidate": c.to_sdp(),
                    "sdpMid": c.sdpMid,
                    "sdpMLineIndex": c.sdpMLineIndex
                }}))

        # 等待结束（通过 ack 后 sender 会主动断）
        await channel_ready.wait()
        await pc._connection_state_complete.wait()  # 保证状态就绪
        await rt
    await pc.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signaling", required=True, help="ws://your-vps-ip:8765")
    parser.add_argument("--room", required=True)
    parser.add_argument("--stun", default="stun:stun.l.google.com:19302")
    parser.add_argument("--turn")
    parser.add_argument("--turn-user")
    parser.add_argument("--turn-pass")
    parser.add_argument("--output", help="save as path (optional)")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--quiet", action="store_true", default=False)
    args = parser.parse_args()
    asyncio.run(run(args))
