import socket
import random
import struct
import sys

def stun_request(server, port):
    # STUN binding request (RFC 5389)
    # Magic cookie 固定 0x2112A442
    # Transaction ID 随机 12 bytes
    tid = bytes(random.getrandbits(8) for _ in range(12))
    msg_type = 0x0001  # Binding Request
    msg_len = 0
    magic_cookie = 0x2112A442

    header = struct.pack("!HHI12s", msg_type, msg_len, magic_cookie, tid)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)

    try:
        sock.sendto(header, (server, port))
        data, _ = sock.recvfrom(2048)
    except socket.timeout:
        print(f"[ERROR] No response from {server}:{port}")
        return
    except Exception as e:
        print(f"[ERROR] Failed to connect {server}:{port} - {e}")
        return

    # 解析 STUN 响应
    if len(data) < 20:
        print("[ERROR] Response too short")
        return

    msg_type, msg_len, magic, rtid = struct.unpack("!HHI12s", data[:20])
    if magic != magic_cookie or rtid != tid:
        print("[ERROR] Invalid STUN response")
        return

    print(f"[OK] Got STUN response from {server}:{port}")
    # 简单解析 XOR-MAPPED-ADDRESS 属性
    body = data[20:]
    i = 0
    while i + 4 <= len(body):
        attr_type, attr_len = struct.unpack("!HH", body[i:i+4])
        val = body[i+4:i+4+attr_len]
        i += 4 + attr_len
        if attr_type == 0x0020:  # XOR-MAPPED-ADDRESS
            port = val[2] ^ (magic_cookie >> 24)
            ip = (
                (val[4] ^ ((magic_cookie >> 24) & 0xFF)),
                (val[5] ^ ((magic_cookie >> 16) & 0xFF)),
                (val[6] ^ ((magic_cookie >> 8) & 0xFF)),
                (val[7] ^ (magic_cookie & 0xFF))
            )
            print(f"[INFO] Your public address via TURN: {'.'.join(map(str, ip))}:{port}")
            return

    print("[INFO] No XOR-MAPPED-ADDRESS found (but server responded)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python check_turn.py <server> <port>")
        sys.exit(1)

    server = sys.argv[1]
    port = int(sys.argv[2])
    stun_request(server, port)
