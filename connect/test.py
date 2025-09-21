#!/usr/bin/env python3
import stun

# 常见 STUN 服务器
STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
    ("stun3.l.google.com", 19302),
    ("stun4.l.google.com", 19302),
    ("stun.miwifi.com", 3478),
    ("stun.sipnet.net", 3478),
    ("stun.3cx.com", 3478),
    ("stun.sipgate.net", 3478),
]

LOCAL_PORT = 54321  # 固定本地端口

def main():
    print(f"Local UDP port fixed at {LOCAL_PORT}")
    print("="*60)

    results = []
    for host, port in STUN_SERVERS:
        try:
            nat_type, ext_ip, ext_port = stun.get_ip_info(
                stun_host=host,
                stun_port=port,
                source_port=LOCAL_PORT
            )
            results.append((host, port, nat_type, ext_ip, ext_port))
            print(f"{host}:{port} => {nat_type}, {ext_ip}:{ext_port}")
        except Exception as e:
            print(f"{host}:{port} => FAILED ({e})")

    print("="*60)
    ports = {r[4] for r in results if r[4]}
    if len(ports) > 1:
        print("结论：不同 STUN 返回的端口不一致 → **对称 NAT**")
    elif len(ports) == 1:
        print("结论：所有 STUN 返回端口一致 → **锥型 NAT（Full/Restricted Cone）**")
    else:
        print("结论：没有成功结果，可能 UDP 被阻断")

if __name__ == "__main__":
    main()
