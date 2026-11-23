import socket
import threading
import time
from core.server import GhostRelayServer
from web.app import app, socketio


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    tcp_server = GhostRelayServer(host="127.0.0.1", port=9090)
    threading.Thread(target=tcp_server.start, daemon=True).start()
    time.sleep(0.3)

    lan_ip = get_local_ip()
    print("[GHOSTLINK WEB BRIDGE] Local: http://127.0.0.1:5000")
    print(f"[GHOSTLINK LAN ACCESS] Phone/Peer: http://{lan_ip}:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)