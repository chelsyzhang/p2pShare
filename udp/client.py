import socket

SERVER_IP = "114.132.234.219"
SERVER_PORT = 2024
LOCAL_PORT = 54321   # 固定端口，方便 NAT 分析

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("", LOCAL_PORT))

sock.sendto(b"hello", (SERVER_IP, SERVER_PORT))
print(f"Sent 'hello' to {SERVER_IP}:{SERVER_PORT} from local port {LOCAL_PORT}")

try:
    data, addr = sock.recvfrom(1024)
    print(f"Got reply {data!r} from {addr}")
except socket.timeout:
    print("No reply (可能 UDP 被屏蔽或 NAT 不可达)")

sock.close()
