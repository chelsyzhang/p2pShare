# common.py - 公共工具：消息、分片等


import os
import math
import hashlib


CHUNK_SIZE = 64 * 1024 # 64KB，可按需调整（越大越快但丢包重传代价也大）

def file_meta(path: str, with_hash=False):
    size = os.path.getsize(path)
    name = os.path.basename(path)
    chunks = math.ceil(size / CHUNK_SIZE)
    meta = {
        "name": name,
        "size": size,
        "chunk_size": CHUNK_SIZE,
        "chunks": chunks,
    }
    if with_hash:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1024 * 1024), b""):
                h.update(b)
        meta["sha256"] = h.hexdigest()
    return meta




def read_chunk(path: str, index: int) -> bytes:
with open(path, "rb") as f:
f.seek(index * CHUNK_SIZE)
return f.read(CHUNK_SIZE)