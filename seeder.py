# seeder.py - 分享端：支持"被动接收 offer"，也支持"超时后主动发起 offer"的双向发起
# 这样即使对方在对称 NAT 下无法主动连入，也有机会由我们先发起，复用“出站映射”上传。

import argparse
import asyncio
import json
import logging
import websockets
from aiortc import RTCIceServer, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.data_channel import RTCDataChannel
from common import file_meta, read_chunk

logging.basicConfig(level=logging.INFO)

ICE_SERVERS = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
]

OFFER_TIMEOUT_SEC = 2.0  # peer-ready 后等待对方先发 offer 的时间，超时则我们主动发起


async def run(signal_url: str, file_id: str, path: str, hash_meta: bool=False):
    meta = file_meta(path, with_hash=hash_meta)
    logging.info("[seeder] file meta: %s", meta)

    async with websockets.connect(signal_url, max_size=2**25) as ws:
        await ws.send(json.dumps({"type": "register", "role": "seeder", "file_id": file_id}))
        logging.info("[seeder] registered, waiting peer ...")

        pc: RTCPeerConnection | None = None
        dc: RTCDataChannel | None = None
        got_remote_offer = asyncio.Event()
        peer_ready = asyncio.Event()

        async def ensure_pc():
            nonlocal pc, dc
            if pc:
                return pc
            pc = RTCPeerConnection(configuration=RTCConfiguration(ICE_SERVERS))

            @pc.on("datachannel")
            def on_datachannel(ch: RTCDataChannel):
                nonlocal dc
                dc = ch
                logging.info("[seeder] datachannel: %s", ch.label)

                @ch.on("message")
                def on_message(data):
                    if isinstance(data, bytes):
                        return
                    try:
                        obj = json.loads(data)
                    except Exception:
                        return
                    t = obj.get("type")
                    if t == "GET_META":
                        ch.send(json.dumps({"type": "META", **meta}))
                    elif t == "GET_CHUNK":
                        idx = int(obj.get("index", 0))
                        ch.send(read_chunk(path, idx))

            @pc.on("icecandidate")
            async def on_icecandidate(event):
                cand = event.candidate
                if cand:
                    await ws.send(json.dumps({
                        "type": "candidate",
                        "file_id": file_id,
                        "candidate": cand.to_sdp(),
                    }))
            return pc

        async def proactive_offer_task():
            # 等待一小段时间，若对方没先发 offer，我们就主动发
            try:
                await asyncio.wait_for(got_remote_offer.wait(), timeout=OFFER_TIMEOUT_SEC)
                return  # 收到远端 offer，就不主动了
            except asyncio.TimeoutError:
                pass
            await ensure_pc()
            # 我们主动创建一个 datachannel，这样出站方向也成立
            nonlocal dc
            if not dc:
                dc = pc.createDataChannel("file")
                @dc.on("open")
                def on_open():
                    logging.info("[seeder] proactive dc opened (waiting client requests)...")
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            await ws.send(json.dumps({
                "type": "offer",
                "file_id": file_id,
                "sdp": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
            }))
            logging.info("[seeder] proactive offer sent")

        # 处理信令
        proactive_task: asyncio.Task | None = None

        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "peer-ready":
                peer_ready.set()
                if proactive_task is None:
                    proactive_task = asyncio.create_task(proactive_offer_task())

            elif t == "offer":
                got_remote_offer.set()
                await ensure_pc()
                offer = RTCSessionDescription(sdp=msg["sdp"]["sdp"], type=msg["sdp"]["type"]) 
                await pc.setRemoteDescription(offer)
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await ws.send(json.dumps({
                    "type": "answer",
                    "file_id": file_id,
                    "sdp": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
                }))
                logging.info("[seeder] answer sent")

            elif t == "answer":
                await ensure_pc()
                answer = RTCSessionDescription(sdp=msg["sdp"]["sdp"], type=msg["sdp"]["type"]) 
                await pc.setRemoteDescription(answer)
                logging.info("[seeder] remote answer set")

            elif t == "candidate":
                await ensure_pc()
                cand_sdp = msg.get("candidate")
                if cand_sdp:
                    await pc.addIceCandidate(cand_sdp)
