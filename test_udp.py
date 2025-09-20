import socket

# 配置两个外部测试目标（必须是不同的 IP）
# 可以改成你自己 VPS 的公网 IP，注意端口要开着 UDP 监听
TARGETS = [
    ("stun.l.google.com", 19302),   # 谷歌 STUN
    ("stun1.l.google.com", 19302)   # 谷歌 STUN 的另一个节点
]

LOCAL_PORT = 54321  # 固定本地端口，便于 NAT 比对

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", LOCAL_PORT))
    sock.settimeout(3)

    for host, port in TARGETS:
        addr = (socket.gethostbyname(host), port)
        message = b"hello"
        sock.sendto(message, addr)
        print(f"Sent to {addr}")

        try:
            data, server = sock.recvfrom(2048)
            print(f"Reply from {server}: {data!r}")
        except socket.timeout:
            print(f"No reply from {addr} (可能是 UDP 被丢弃)")
    sock.close()

if __name__ == "__main__":
    main()
