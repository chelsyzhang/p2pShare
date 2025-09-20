import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 2024))   # 在 VPS 监听 3478 端口
print("UDP echo server listening on port 2024...")

while True:
    data, addr = sock.recvfrom(1024)
    print(f"got {data!r} from {addr}")
    sock.sendto(data, addr)   # 原样回发