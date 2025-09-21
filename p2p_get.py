import argparse
import asyncio
import hashlib
import json
import os
import logging
import websockets
import time
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration

CHUNK_LIMIT_FOR_FLUSH = 4 * 1024 * 1024  # 每 4MB flush 一次

# ============ 日志配置 ============
logging.basicConfig(
    level=logging.DEBUG,   # 改成 INFO 会少点输出
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("fetch")


def human(n):
    """把字节数转换成人类可读格式"""
    units = ["B", "KB", "MB", "GB", "TB"]
    for u in units:
        if n < 1024:
            return f"{n:.2f}{u}"
        n /= 1024
    return f"{n:.2f}PB"


async def run(args):
    ice_servers = []
    if args.stun:
        ice_servers.append(RTCIceServer(urls=[args.stun]))
    if args.turn and args.turn_user and args.turn_pass:
        ice_servers.append(RTCIceServer(urls=[args.turn],
                                        username=args.turn_user,
                                        credential=args.turn_pass))
    logger.debug("Using ICE servers: %s", ice_servers)

    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    file = None
    sha256 = hashlib.sha256()
    expect_size = None
    recv_size = 0
    out_path = None
    buffered = 0
    channel_ready = asyncio.Event()
    sender_channel = None
    start_ts = time.time()

    @pc.on("datachannel")
    def on_datachannel(ch):
        nonlocal sender_channel
        sender_channel = ch
        logger.info("DataChannel received: %s", ch.label)

        @ch.on("open")
        def _open():
            logger.info("DataChannel opened")
            channel_ready.set()

        @ch.on("message")
        def _msg(msg):
            nonlocal file, expect_size, recv_size, out_path, buffered, start_ts
            if isinstance(msg, bytes):
                if file is None:
                    logger.warning("Received binary data before meta, ignoring")
                    return
                file.write(msg)
                sha256.update(msg)
                recv_size += len(msg)
                buffered += len(msg)

                if buffered >= CHUNK_LIMIT_FOR_FLUSH:
                    file.flush()
                    os.fsync(file.fileno())
                    buffered = 0

                if not args.quiet and expect_size:
                    pct = recv_size / expect_size * 100
                    elapsed = time.time() - start_ts
                    speed = recv_size / max(elapsed, 1e-6)
                    print(
                        f"\rReceiving {os.path.basename(out_path)}: "
                        f"{human(recv_size)}/{human(expect_size)} "
                        f"({pct:.1f}%) avg {human(speed)}/s",
                        end=""
                    )
            else:
                try:
                    j = json.loads(msg)
                except Exception:
                    logger.error("Invalid text message: %s", msg)
                    return

                if j.get("kind") == "meta":
                    name = j["name"]
                    expect_size = int(j["size"])
                    out_name = args.output if args.output else name
                    out_path = os.path.abspath(out_name)
                    if os.path.exists(out_path) and not args.overwrite:
                        base, ext = os.path.splitext(out_path)
                        k = 1
                        while os.path.exists(out_path):
                            out_path = f"{base}.recv{'' if k==1 else k}{ext}"
                            k += 1
                    file = open(out_path, "wb")
                    recv_size = 0
                    start_ts = time.time()
                    logger.info("Receiving file: %s (expect %d bytes)", out_path, expect_size)
                elif j.get("kind") == "eof":
                    remote_digest = j["sha256"]
                    if file:
                        file.flush()
                        os.fsync(file.fileno())
                        file.close()
                        file = None
                    local_digest = sha256.hexdigest()
                    print()  # 换行，避免进度和日志挤一行
                    logger.info("Transfer complete, local sha256=%s", local_digest)
                    if remote_digest != local_digest:
                        logger.warning("SHA256 mismatch! remote=%s", remote_digest)
                    else:
                        logger.info("SHA256 verified OK")
                    ch.send(json.dumps({"kind": "ack"}))

    @pc.on("iceconnectionstatechange")
    def on_ice():
        logger.info("ICE state changed: %s", pc.iceConnectionState)

    @pc.on("signalingstatechange")
    def on_sig():
        logger.debug("Signaling state: %s", pc.signalingState)

    @pc.on("icegatheringstatechange")
    def on_ice_gather():
        logger.debug("ICE gathering state: %s", pc.iceGatheringState)

    async with websockets.connect(args.signaling) as ws:
        logger.info("Connected to signaling server: %s", args.signaling)
        await ws.send(json.dumps({"type": "join", "room": args.room, "role": "receiver"}))
        logger.debug("Join request sent for room %s", args.room)

        # 等待 joined
        while True:
            msg = json.loads(await ws.recv())
            logger.debug("Signaling message: %s", msg)
            if msg.get("type") == "joined":
                logger.info("Joined room: %s as receiver", args.room)
                break

        async def recv_task():
            async for raw in ws:
                m = json.loads(raw)
                logger.debug("Recv signaling: %s", m)
                if m["type"] == "sdp":
                    sdp = m["data"]
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"])
                    )
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    await ws.send(json.dumps({
                        "type": "sdp",
                        "data": {
                            "sdp": pc.localDescription.sdp,
                            "type": pc.localDescription.type
                        }
                    }))
                    logger.info("Sent SDP answer")
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

        await channel_ready.wait()
        logger.info("Channel ready, waiting for file transfer...")

        await rt
    await pc.close()
    logger.info("PeerConnection closed")


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
