# p2p_get.py - 下载端：连接信令，发起 offer，经 DataChannel 拉取分片并落盘

import argparse
import asyncio
import json
import logging
import websockets
from aiortc import RTCIceServer, RTCConfiguration, RTCPeerConnection
from aiortc import RTCSessionDescription
from aiortc.data_channel import RTCDataChannel

logging.basicConfig(level=logging.INFO)

ICE_SERVERS = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
]


class Downloader:
    def __init__(self, out_path: str):
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
        # 顺序写
        self.file.write(b)
        self.received += len(b)

    def progress(self):
        if not self.meta:
            return "?"
        size = self.meta["size"]
        pct = (self.received / size) * 100 if size else 0
        return f"{self.received}/{size} bytes ({pct:.2f}%)"


async def run(signal_url: str, file_id: str, out_path: str = None):
    async with websockets.connect(signal_url, max_size=2**25) as ws:
        await ws.send(json.dumps({"type": "register", "role": "client", "file_id": file_id}))
        logging.info("[client] registered, waiting peer ...")

        pc = RTCPeerConnection(configuration=RTCConfiguration(ICE_SERVERS))
        channel = pc.createDataChannel("file")
        dl = Downloader(out_path)

        ready = asyncio.Event()
        meta_ready = asyncio.Event()

        @channel.on("open")
        def on_open():
            logging.info("[client] datachannel open, requesting META ...")
            channel.send(json.dumps({"type": "GET_META"}))

        @channel.on("message")
        def on_message(data):
            if isinstance(data, bytes):
                # 分片数据
                dl.write_chunk(data)
                if dl.next_index + 1 < dl.meta["chunks"]:
                    dl.next_index += 1
                    channel.send(json.dumps({"type": "GET_CHUNK", "index": dl.next_index}))
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
                    path = dl.open(obj["name"])
                    logging.info("[client] META: name=%s size=%d chunks=%d", obj["name"], obj["size"], obj["chunks"])
                    meta_ready.set()
                    # 请求第 0 片
                    channel.send(json.dumps({"type": "GET_CHUNK", "index": 0}))

        @pc.on("icecandidate")
        async def on_icecandidate(event):
            cand = event.candidate
            if cand:
                await ws.send(json.dumps({
                    "type": "candidate",
                    "file_id": file_id,
                    "candidate": cand.to_sdp(),
                }))

        # 生成 offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await ws.send(json.dumps({
            "type": "offer",
            "file_id": file_id,
            "sdp": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
        }))

        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "answer":
                answer = RTCSessionDescription(sdp=msg["sdp"]["sdp"], type=msg["sdp"]["type"])
                await pc.setRemoteDescription(answer)
            elif t == "candidate":
                cand_sdp = msg.get("candidate")
                if cand_sdp:
                    await pc.addIceCandidate(cand_sdp)

            if ready.is_set():
                break

        await ready.wait()
        dl.close()
        logging.info("[client] saved, %s", dl.progress())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal", required=True, help="ws://host:port")
    parser.add_argument("--file-id", required=True)
    parser.add_argument("-o", "--output", default=None, help="输出文件名（默认使用原名）")
    args = parser.parse_args()
    asyncio.run(run(args.signal, args.file_id, args.output))