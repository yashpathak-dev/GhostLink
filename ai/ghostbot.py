import re
from collections import Counter


class GhostBot:
    """
    100% local, zero-API ephemeral assistant operating strictly on in-memory session frames.
    """

    STOPWORDS = {
        "the", "is", "in", "at", "of", "on", "and", "a", "to", "for", "with",
        "that", "this", "it", "are", "was", "be", "as", "by", "or", "an", "if",
        "from", "but", "not", "so", "we", "you", "i", "me", "my", "your", "they",
    }

    @staticmethod
    def handle_command(command_text: str, ram_messages: list[dict]) -> str:
        raw = command_text.strip()
        cmd = raw.lower()

        if cmd == "/ghost status":
            return (
                f"Tunnel Active | Local RAM Buffer: {len(ram_messages)} decrypted frames | "
                f"Engine: 100% Local Extractive NLP | External API Calls: 0 | Disk Footprint: 0 bytes."
            )

        if cmd == "/ghost summary":
            return GhostBot._extractive_summary(ram_messages)

        if cmd == "/ghost links":
            urls = []
            for m in ram_messages:
                urls.extend(re.findall(r"https?://[^\s]+", m["text"]))
            if not urls:
                return "No URLs detected in current volatile RAM buffer."
            return "Extracted Links in RAM:\n• " + "\n• ".join(dict.fromkeys(urls))

        if cmd.startswith("/ghost search "):
            term = raw[14:].strip().lower()
            if not term:
                return "Usage: /ghost search <keyword>"
            matches = [
                f"@{m['sender']}: {m['text']}"
                for m in ram_messages
                if term in m["text"].lower()
            ]
            if not matches:
                return f"No messages matching '{term}' in active RAM buffer."
            return f"RAM Matches for '{term}' ({len(matches)}):\n• " + "\n• ".join(matches[-5:])

        return "Commands: /ghost summary | /ghost status | /ghost links | /ghost search <word> | /flush"

    @classmethod
    def _extractive_summary(cls, ram_messages: list[dict]) -> str:
        if not ram_messages:
            return "No active plaintext messages in local RAM buffer to summarize."

        participants = sorted({m["sender"] for m in ram_messages})
        msg_count = len(ram_messages)

        words = []
        for m in ram_messages:
            tokens = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", m["text"].lower())
            words.extend(t for t in tokens if t not in cls.STOPWORDS)

        freq = Counter(words)
        top_keywords = [w for w, _ in freq.most_common(4)]

        scored = []
        for idx, m in enumerate(ram_messages):
            tokens = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", m["text"].lower())
            score = sum(freq[t] for t in tokens if t in freq)
            scored.append((score, idx, f"@{m['sender']}: {m['text'][:85]}"))

        top_messages = [item[2] for item in sorted(scored, key=lambda x: (-x[0], x[1]))[:3]]
        kw_str = ", ".join(top_keywords) if top_keywords else "general chat"

        return (
            f"Local RAM Digest ({msg_count} msgs | Peers: {', '.join(participants)} | Topics: {kw_str}):\n"
            + "\n".join(f"• {line}" for line in top_messages)
        )