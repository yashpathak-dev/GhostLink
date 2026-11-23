import socket
import threading
import json
from collections import deque

HOST = "127.0.0.1"
PORT = 9090
MAX_RAM_MESSAGES = 50


class GhostRelayServer:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Volatile RAM state: { room_id: {"clients": {conn: alias}, "buffer": deque()} }
        self.rooms = {}
        self.lock = threading.Lock()

    def start(self):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(100)
        print(f"[GHOSTLINK TCP SERVER] Listening on {self.host}:{self.port} (RAM-ONLY MODE)")

        while True:
            conn, addr = self.server_socket.accept()
            thread = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
            thread.start()

    def _handle_client(self, conn: socket.socket, addr):
        current_room = None
        alias = "anon"
        buffer = ""

        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                buffer += data.decode("utf-8")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    packet = json.loads(line)
                    action = packet.get("action")

                    if action == "JOIN":
                        current_room = packet["room"]
                        alias = packet.get("alias", "anon")
                        self._register_client(current_room, conn, alias)

                    elif action in ("MSG", "FILE") and current_room:
                        self._broadcast(current_room, packet)

                    elif action == "TYPING" and current_room:
                        self._broadcast(current_room, packet, exclude=conn, cache=False)

                    elif action == "FLUSH" and current_room:
                        self._flush_room(current_room)

        except (ConnectionResetError, json.JSONDecodeError):
            pass
        finally:
            if current_room:
                self._unregister_client(current_room, conn, alias)
            conn.close()

    def _register_client(self, room: str, conn: socket.socket, alias: str):
        with self.lock:
            if room not in self.rooms:
                self.rooms[room] = {
                    "clients": {},
                    "buffer": deque(maxlen=MAX_RAM_MESSAGES),
                }
            self.rooms[room]["clients"][conn] = alias
            history = [
                pkt for pkt in self.rooms[room]["buffer"]
                if int(pkt.get("ttl", 0)) == 0
            ]
            peer_list = list(self.rooms[room]["clients"].values())

        for cached_packet in history:
            self._send_safe(conn, cached_packet)

        self._broadcast(room, {"action": "PEERS", "peers": peer_list}, cache=False)
        self._broadcast(room, {
            "action": "SYS",
            "room": room,
            "sender": "SYSTEM",
            "payload": f"@{alias} connected via encrypted TCP tunnel.",
        }, cache=False)

    def _unregister_client(self, room: str, conn: socket.socket, alias: str):
        room_empty = False
        peer_list = []
        with self.lock:
            if room in self.rooms:
                self.rooms[room]["clients"].pop(conn, None)
                if len(self.rooms[room]["clients"]) == 0:
                    self.rooms[room]["buffer"].clear()
                    del self.rooms[room]
                    room_empty = True
                else:
                    peer_list = list(self.rooms[room]["clients"].values())

        if room_empty:
            print(f"[RAM WIPE] Room {room} emptied. Volatile memory purged.")
        else:
            self._broadcast(room, {"action": "PEERS", "peers": peer_list}, cache=False)
            self._broadcast(room, {
                "action": "SYS",
                "room": room,
                "sender": "SYSTEM",
                "payload": f"@{alias} disconnected.",
            }, cache=False)

    def _broadcast(self, room: str, packet: dict, exclude=None, cache: bool = True):
        encoded = (json.dumps(packet) + "\n").encode("utf-8")
        with self.lock:
            if room not in self.rooms:
                return
            if cache and packet.get("action") == "MSG" and int(packet.get("ttl", 0)) == 0:
                self.rooms[room]["buffer"].append(packet)
            targets = list(self.rooms[room]["clients"].keys())

        for client_conn in targets:
            if client_conn != exclude:
                self._send_safe(client_conn, encoded, already_encoded=True)

    def _flush_room(self, room: str):
        with self.lock:
            if room in self.rooms:
                self.rooms[room]["buffer"].clear()
        self._broadcast(room, {
            "action": "SYS",
            "room": room,
            "sender": "SYSTEM",
            "payload": "Volatile RAM buffer manually flushed by peer.",
        }, cache=False)

    @staticmethod
    def _send_safe(conn: socket.socket, payload, already_encoded: bool = False):
        try:
            data = payload if already_encoded else (json.dumps(payload) + "\n").encode("utf-8")
            conn.sendall(data)
        except OSError:
            pass


if __name__ == "__main__":
    GhostRelayServer().start()