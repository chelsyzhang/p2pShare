# seeder.py - 本地分享端：连接信令，等待客户端，走 WebRTC DataChannel 按需发送分片

import argparse
import asyncio
import json
import logging
import websockets
from aiortc import RTCIceServer, RTCConfiguration, RTCPeerConnection
from aiortc.contrib.signaling import BYE
from aiortc import RTCSessionDescription
from aiortc import RTCDataChannel
from common import file_meta, read_chunk

logging.basicConfig(level=logging.INFO)

ICE_SERVERS = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
]

async def run(signal_url: str, file_id: str, path: str, hash_meta: bool=False):
    meta = file_meta(path, with_hash=hash_meta)
    logging.info("[seeder] file meta: %s", meta)

    async with websockets.connect(signal_url, max_size=2**25) as ws:
        await ws.send(json.dumps({"type": "register", "role": "seeder", "file_id": file_id}))
        logging.info("[seeder] registered, waiting client ...")

        pc = None
        channel = None

        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "peer-ready":
            # 等客户端 offer
                logging.info("[seeder] peer ready: %s", msg)

            elif t == "offer":
                # 创建 peer，设置 remote desc，创建 answer
                logging.info("[seeder] got offer")
                pc = RTCPeerConnection(configuration=RTCConfiguration(ICE_SERVERS))

                @pc.on("datachannel")
                def on_datachannel(dc: RTCDataChannel):
                    nonlocal channel
                    channel = dc
                    logging.info("[seeder] datachannel: %s", dc.label)

                    @dc.on("message")
                    def on_message(data):
                    # data 可能为 bytes 或 str
                        if isinstance(data, bytes):
                        # 客户端不会发二进制命令
                            return
                        try:
                            obj = json.loads(data)
                        except Exception:
                            return
                        cmd = obj.get("type")
                        if cmd == "GET_META":
                            dc.send(json.dumps({"type": "META", **meta}))
                        elif cmd == "GET_CHUNK":
                            index = int(obj.get("index", 0))
                            payload = read_chunk(path, index)
                            dc.send(payload)
                @pc.on("icecandidate")
                async def on_icecandidate(event):
                    cand = event.candidate
                    if cand:
                        await ws.send(json.dumps({
                            "type": "candidate",
                            "file_id": file_id,
                            "candidate": cand.to_sdp(),
                            }))
                offer = RTCSessionDescription(sdp=msg["sdp"]["sdp"], type=msg["sdp"]["type"])
                await pc.setRemoteDescription(offer)
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await ws.send(json.dumps({
                "type": "answer",
                "file_id": file_id,
                "sdp": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
                }))
                logging.info("[seeder] sent answer, waiting for requests ...")
            elif t == "candidate":
                if pc and msg.get("candidate"):
                    # aiortc 的 from_sdp 用法：pc.addIceCandidate(candidate)
                    cand_sdp = msg["candidate"]
                    # aiortc 允许直接传字符串 SDP（兼容）
                    await pc.addIceCandidate(cand_sdp)


    logging.info("[seeder] signaling closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal", required=True, help="ws://host:port")
    parser.add_argument("--file", required=True)
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--hash", action="store_true", help="include sha256 in META")
    args = parser.parse_args()
    asyncio.run(run(args.signal, args.file_id, args.file, args.hash))
