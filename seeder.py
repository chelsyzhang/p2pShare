import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import websockets
from aiortc import RTCPeerConnection, RTCIceServer, RTCConfiguration, RTCSessionDescription
from aiortc.contrib.signaling import BYE

# ================= 日志配置 =================
logging.basicConfig(
    level=logging.DEBUG,   # 调整为 INFO 可以少点输出
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("seeder")

# ================= 常量 =================
CHUNK_SIZE = 64 * 1024  # 每次发送 64KB


def human(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.2f}{unit}"
        n /= 1024
    return f"{n:.2f}PB"


async def run(args):
    logger.info("Seeder started, preparing file: %s", args.file)

    # ========= ICE 服务器配置 =========
    ice_servers = []
    if args.stun:
        ice_servers.append(RTCIceServer(urls=[args.stun]))
    if args.turn and args.turn_user and args.turn_pass:
        ice_servers.append(RTCIceServer(
            urls=[args.turn],
            username=args.turn_user,
            credential=args.turn_pass
        ))

    logger.debug("Using ICE servers: %s", ice_servers)

    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    channel = pc.createDataChannel("file", ordered=True)
    logger.info("DataChannel created, label=%s", channel.label)

    done_fut = asyncio.get_event_loop().create_future()
    file_size = os.path.getsize(args.file)
    file_name = os.path.basename(args.file)

    # ========== 文件发送 ==========
    async def send_file():
        sha256 = hashlib.sha256()
        sent = 0
        start_ts = asyncio.get_event_loop().time()
        with open(args.file, "rb") as f:
            meta = {"kind": "meta", "name": file_name, "size": file_size}
            channel.send(json.dumps(meta))
            logger.info("Sent file metadata: %s", meta)

            while True:
                while channel.bufferedAmount > 8 * CHUNK_SIZE:
                    await asyncio.sleep(0.01)

                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
                channel.send(chunk)
                sent += len(chunk)

                if not args.quiet:
                    pct = sent / file_size * 100 if file_size > 0 else 100
                    sys.stdout.write(
                        f"\rSending {file_name}: {human(sent)}/{human(file_size)} ({pct:.1f}%)"
                    )
                    sys.stdout.flush()

            digest = sha256.hexdigest()
            channel.send(json.dumps({"kind": "eof", "sha256": digest}))
            logger.info("File transfer complete, sha256=%s", digest)

            if not args.quiet:
                dt = asyncio.get_event_loop().time() - start_ts
                rate = sent / max(dt, 1e-6)
                sys.stdout.write(
                    f"\nDone in {dt:.2f}s, avg {human(rate)}/s, sha256={digest}\n"
                )
                sys.stdout.flush()

    # ========== DataChannel 回调 ==========
    @channel.on("open")
    def on_open():
        logger.info("DataChannel opened, start sending file")
        asyncio.ensure_future(send_file())

    @channel.on("message")
    def on_message(msg):
        try:
            j = json.loads(msg) if isinstance(msg, str) else None
        except Exception:
            j = None
        if isinstance(j, dict) and j.get("kind") == "ack":
            logger.info("Received ACK from receiver, transfer confirmed")
            if not done_fut.done():
                done_fut.set_result(True)
        else:
            logger.debug("Message from peer: %s", str(msg)[:100])

    # ========== ICE 状态日志 ==========
    @pc.on("iceconnectionstatechange")
    def on_ice():
        logger.info("ICE connection state changed: %s", pc.iceConnectionState)
        if pc.iceConnectionState in ("failed", "disconnected", "closed"):
            if not done_fut.done():
                done_fut.set_exception(
                    RuntimeError(f"ICE {pc.iceConnectionState}")
                )

    @pc.on("signalingstatechange")
    def on_sig():
        logger.debug("Signaling state changed: %s", pc.signalingState)

    @pc.on("icegatheringstatechange")
    def on_ice_gather():
        logger.debug("ICE gathering state changed: %s", pc.iceGatheringState)

    # ========== 信令服务器 ==========
    async with websockets.connect(args.signaling) as ws:
        logger.info("Connected to signaling server: %s", args.signaling)
        await ws.send(json.dumps(
            {"type": "join", "room": args.room, "role": "sender"}
        ))
        logger.debug("Join request sent for room %s", args.room)

        joined = json.loads(await ws.recv())
        logger.info("Join ack: %s", joined)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        logger.debug("Created local SDP offer")

        await ws.send(json.dumps({
            "type": "sdp",
            "data": {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type
            }
        }))
        logger.info("Sent local SDP offer")

        async def recv_task():
            async for raw in ws:
                m = json.loads(raw)
                if m["type"] == "sdp":
                    sdp = m["data"]
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"])
                    )
                    logger.info("Received remote SDP and set description")
                elif m["type"] == "ice":
                    cand = m["data"]
                    await pc.addIceCandidate(cand)
                    logger.debug("Added remote ICE candidate")

        rt = asyncio.create_task(recv_task())

        @pc.on("icecandidate")
        async def on_candidate(c):
            if c:
                await ws.send(json.dumps({
                    "type": "ice",
                    "data": {
                        "candidate": c.to_sdp(),
                        "sdpMid": c.sdpMid,
                        "sdpMLineIndex": c.sdpMLineIndex
                    }
                }))
                logger.debug("Sent local ICE candidate")

        # 等待传输完成
        await done_fut
        await ws.send(BYE)
        logger.info("Sent BYE to signaling server")
        rt.cancel()

    await pc.close()
    logger.info("PeerConnection closed")


# ================= 主函数入口 =================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signaling", required=True, help="ws://your-vps-ip:8765")
    parser.add_argument("--room", required=True, help="room id (any string)")
    parser.add_argument("--file", required=True, help="path of file to send")
    parser.add_argument("--stun", default="stun:stun.l.google.com:19302", help="STUN url")
    parser.add_argument("--turn", help="TURN url, e.g. turn:your-vps:2025?transport=udp")
    parser.add_argument("--turn-user", help="TURN username")
    parser.add_argument("--turn-pass", help="TURN password")
    parser.add_argument("--quiet", action="store_true", default=False)
    args = parser.parse_args()

    asyncio.run(run(args))
