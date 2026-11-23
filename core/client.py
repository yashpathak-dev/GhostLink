import socket
import threading
import json
import time
from core.crypto import EphemeralCipher
from ai.privacy_guard import PrivacyGuard
from ai.ghostbot import GhostBot


class GhostTCPClient:
    def __init__(self, alias: str, room: str, secret_key: str, on_message_callback, host="127.0.0.1", port=9090):
        self.alias = alias
        self.room = room
        self.cipher = EphemeralCipher(secret_key)
        self.on_message = on_message_callback
        self.local_ram_log = []

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.running = True

        threading.Thread(target=self._listen, daemon=True).start()
        self._send_raw({"action": "JOIN", "room": self.room, "alias": self.alias})

    def send_chat(self, plaintext: str, ttl: int = 0) -> dict:
        if plaintext.strip().startswith("/ghost"):
            reply = GhostBot.handle_command(plaintext, self.local_ram_log)
            return {"status": "bot", "message": reply}

        if plaintext.strip() == "/flush":
            self.local_ram_log.clear()
            self._send_raw({"action": "FLUSH", "room": self.room})
            return {"status": "ok"}

        safe, warning = PrivacyGuard.inspect(plaintext)
        if not safe:
            return {"status": "blocked", "message": warning}

        ciphertext = self.cipher.encrypt(plaintext).decode("utf-8")
        self._send_raw({
            "action": "MSG",
            "room": self.room,
            "sender": self.alias,
            "ciphertext": ciphertext,
            "ttl": int(ttl),
        })
        return {"status": "ok"}

    def send_file(self, filename: str, mime_type: str, base64_data: str, ttl: int = 0):
        encrypted_blob = self.cipher.encrypt(base64_data).decode("utf-8")
        self._send_raw({
            "action": "FILE",
            "room": self.room,
            "sender": self.alias,
            "filename": filename,
            "mime_type": mime_type,
            "ciphertext": encrypted_blob,
            "ttl": int(ttl),
        })

    def send_typing(self):
        self._send_raw({
            "action": "TYPING",
            "room": self.room,
            "sender": self.alias,
        })

    def _schedule_ram_burn(self, entry: dict, ttl: int):
        if ttl <= 0:
            return

        def burn():
            time.sleep(ttl)
            if entry in self.local_ram_log:
                self.local_ram_log.remove(entry)

        threading.Thread(target=burn, daemon=True).start()

    def _listen(self):
        buffer = ""
        while self.running:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    packet = json.loads(line)
                    action = packet.get("action")

                    if action == "MSG":
                        decrypted = self.cipher.decrypt(packet["ciphertext"].encode("utf-8"))
                        ttl = int(packet.get("ttl", 0))
                        entry = {"sender": packet["sender"], "text": decrypted}
                        self.local_ram_log.append(entry)
                        self._schedule_ram_burn(entry, ttl)

                        self.on_message({
                            "type": "chat",
                            "sender": packet["sender"],
                            "text": decrypted,
                            "cipher_preview": packet["ciphertext"][:28] + "...",
                            "ttl": ttl,
                        })

                    elif action == "FILE":
                        decrypted_b64 = self.cipher.decrypt(packet["ciphertext"].encode("utf-8"))
                        self.on_message({
                            "type": "file",
                            "sender": packet["sender"],
                            "filename": packet["filename"],
                            "mime_type": packet["mime_type"],
                            "data_url": decrypted_b64,
                            "ttl": int(packet.get("ttl", 0)),
                        })

                    elif action == "PEERS":
                        self.on_message({
                            "type": "peers",
                            "peers": packet.get("peers", []),
                        })

                    elif action == "TYPING":
                        self.on_message({
                            "type": "typing",
                            "sender": packet.get("sender", "peer"),
                        })

                    elif action == "SYS":
                        self.on_message({
                            "type": "sys",
                            "sender": "SYSTEM",
                            "text": packet["payload"],
                        })
            except Exception:
                break

    def _send_raw(self, obj: dict):
        self.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def disconnect(self):
        self.running = False
        self.local_ram_log.clear()
        self.sock.close()