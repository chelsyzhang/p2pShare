# 简单信令服务器，用 TCP 转发两端的公网地址
import socket
import threading
import json

HOST = "0.0.0.0"
PORT = 2024

clients = {}

def handle_client(conn, addr):
    print("connected", addr)
    peer_id = conn.recv(1024).decode().strip()
    clients[peer_id] = conn
    print(peer_id, "registered")

    while True:
        try:
            data = conn.recv(1024)
            if not data:
                break
            msg = json.loads(data.decode())
            target = msg.get("to")
            if target in clients:
                clients[target].send(data)
        except Exception:
            break

    conn.close()
    if peer_id in clients:
        clients.pop(peer_id)
    print(peer_id, "closed")

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, PORT))
    s.listen()
    print("signal server on", (HOST, PORT))
    while True:
        conn, addr = s.accept()
        threading.Thread(target=handle_client, args=(conn, addr)).start()

if __name__ == "__main__":
    main()
