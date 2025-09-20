# p2p_get.py - 下载端：默认主动发起 offer；但也能接收对方主动的 offer（避免“撞车”）
# 采用“简化版 Perfect Negotiation”：若在我们创建本地 offer 前收到远端 offer，则先当被动端应答。

import argparse
import asyncio
import json
import logging
import websockets
from aiortc import RTCIceServer, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.data_channel import RTCDataChannel

logging.basicConfig(level=logging.INFO)

ICE_SERVERS = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
]

class Downloader:
    def __init__(self, out_path: str | None):
        self.out_path = out_path
        self.meta = None
        self.file = None
        self.next_index = 0
        self.received = 0

    def open(self, name: str):
        path = self.out_path or name
        self.file = open(path, "wb")
        return path

    def close(self):
        if self.file:
            self.file.close()

    def write_chunk(self, b: bytes):
        self.file.write(b)
        self.received += len(b)

    def progress(self):
        if not self.meta:
            return "?"
        size = self.meta["size"]
        pct = (self.received / size) * 100 if size else 0
        return f"{self.received}/{size} bytes ({pct:.2f}%)"

async def run(signal_url: str, file_id: str, out_path: str | None = None):
    async with websockets.connect(signal_url, max_size=2**25) as ws:
        await ws.send(json.dumps({"type": "register", "role": "client", "file_id": file_id}))
        logging.info("[client] registered, waiting peer ...")

        pc = RTCPeerConnection(configuration=RTCConfiguration(ICE_SERVERS))
        dc: RTCDataChannel | None = None
        dl = Downloader(out_path)

        made_local_offer = asyncio.Event()
        got_remote_offer = asyncio.Event()
        ready = asyncio.Event()

        def ensure_dc():
            nonlocal dc
            if not dc:
                dc = pc.createDataChannel("file")
                @dc.on("open")
                def on_open():
                    logging.info("[client] datachannel open, requesting META ...")
                    dc.send(json.dumps({"type": "GET_META"}))
                @dc.on("message")
                def on_message(data):
                    if isinstance(data, bytes):
                        dl.write_chunk(data)
                        if dl.next_index + 1 < dl.meta["chunks"]:
                            dl.next_index += 1
                            dc.send(json.dumps({"type": "GET_CHUNK", "index": dl.next_index}))
                        else:
                            logging.info("[client] download done: %s", dl.progress())
                            ready.set()
                    else:
                        try:
                            obj = json.loads(data)
                        except Exception:
                            return
                        if obj.get("type") == "META":
                            dl.meta = obj
                            dl.open(obj["name"])
                            logging.info("[client] META: name=%s size=%d chunks=%d", obj["name"], obj["size"], obj["chunks"])
                            dc.send(json.dumps({"type": "GET_CHUNK", "index": 0}))

        @pc.on("icecandidate")
        async def on_icecandidate(event):
            cand = event.candidate
            if cand:
                await ws.send(json.dumps({
                    "type": "candidate",
                    "file_id": file_id,
                    "candidate": cand.to_sdp(),
                }))

        async def make_offer():
            # 只有在没收到远端 offer 的情况下才主动发
            if not got_remote_offer.is_set():
                ensure_dc()
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
                await ws.send(json.dumps({
                    "type": "offer",
                    "file_id": file_id,
                    "sdp": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
                }))
                made_local_offer.set()
                logging.info("[client] local offer sent")

        # 等待 peer-ready 再决定是否先手
        peer_ready = asyncio.Event()

        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "peer-ready":
                peer_ready.set()
                # 小延迟，给对端先手机会，降低“撞车”概率
                await asyncio.sleep(0.5)
                # 若还没收到远端 offer，则我们主动发
                asyncio.create_task(make_offer())

            elif t == "offer":
                got_remote_offer.set()
                offer = RTCSessionDescription(sdp=msg["sdp"]["sdp"], type=msg["sdp"]["type"]) 
                await pc.setRemoteDescription(offer)
                # 我们现在作为被动端应答
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await ws.send(json.dumps({
                    "type": "answer",
                    "file_id": file_id,
                    "sdp": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
                }))
                logging.info("[client] answer sent")

            elif t == "answer":
                answer = RTCSessionDescription(sdp=msg["sdp"]["sdp"], type=msg["sdp"]["type"]) 
                await pc.setRemoteDescription(answer)
                logging.info("[client] remote answer set")

            elif t == "candidate":
                cand_sdp = msg.get("candidate")
                if cand_sdp:
                    await pc.addIceCandidate(cand_sdp)

            # 如果我们已经创建了本地 offer，确保有数据通道
            if made_local_offer.is_set() and not dc:
                ensure_dc()

            if ready.is_set():
                break

        dl.close()
        logging.info("[client] saved, %s", dl.progress())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal", required=True, help="ws://host:port")
    parser.add_argument("--file-id", required=True)
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()
    asyncio.run(run(args.signal, args.file_id, args.output))