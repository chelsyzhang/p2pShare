import socket
import threading
import json
import sys

SIGNAL_IP = "114.132.234.219"
SIGNAL_PORT = 2024


def listen_udp(sock):
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            print("got from", addr, ":", data.decode())
        except Exception:
            break

def main(peer_id, target_id):
    # 1. UDP socket
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(("", 0))
    local_port = udp_sock.getsockname()[1]

    # 2. TCP 连接信令服务器
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.connect((SIGNAL_IP, SIGNAL_PORT))
    tcp_sock.sendall(peer_id.encode() + b"\n")

    # 3. 把自己的公网地址发给对方（由信令转发）
    msg = {"to": target_id, "host": SIGNAL_IP, "port": local_port}
    tcp_sock.sendall(json.dumps(msg).encode())

    # 4. 接收对方的地址
    def tcp_listener():
        while True:
            try:
                data = tcp_sock.recv(1024)
                if not data:
                    break
                msg = json.loads(data.decode())
                peer_host = msg["host"]
                peer_port = msg["port"]
                print("peer addr:", peer_host, peer_port)
                # 5. 尝试打洞（不停发包）
                def punch():
                    for i in range(10):
                        udp_sock.sendto(f"hello {i} from {peer_id}".encode(),
                                        (peer_host, peer_port))
                threading.Thread(target=punch).start()
            except Exception:
                break

    threading.Thread(target=tcp_listener, daemon=True).start()
    threading.Thread(target=listen_udp, args=(udp_sock,), daemon=True).start()

    # 保持运行
    while True:
        try:
            udp_sock.sendto(f"keepalive from {peer_id}".encode(),
                            (SIGNAL_IP, SIGNAL_PORT))
        except Exception:
            break

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python peer.py <peer_id> <target_id>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
