import logging
from black_tape_engine.legacy_core.base_scanner import BaseScanner

logger = logging.getLogger("BLACK-TAPE.SCANNER.CHAT")

class ChatScanner(BaseScanner):
    """
    Recursive Signal Extraction.
    Tunnels through nested JSON trees to identify message-like dictionaries.
    """

    def __init__(self):
        # The 'Scent': Keys that indicate a dictionary is actually a message
        self.content_indicators = {'text', 'Content', 'body', 'message', 'data', 'Media Type'}
        self.sender_indicators = {'sender', 'From', 'author', 'sender_name', 'FromMe'}

    def scan(self, filename, data):
        """
        Entry point for the Orchestrator.
        Uses recursion to find signals regardless of JSON depth.
        """
        if any(x in filename.lower() for x in ["location", "gps", "points"]):
            return []

        logger.info(f"[Job Signal] Initiating Recursive Hunt in: {filename}")
        extracted = []

        # Start the recursive search
        self._recursive_search(data, "GENERAL_SIGNAL", extracted)

        logger.info(f"[Job Signal] Siphoned {len(extracted)} signals from {filename}")
        return extracted

    def _recursive_search(self, node, current_context, results):
        """
        Walks the entire JSON tree.
        """
        if isinstance(node, dict):
            # 1. Check if THIS dictionary is a message
            if any(key in node for key in self.content_indicators):
                results.append(self._process_item(current_context, node))
            else:
                # 2. Otherwise, keep digging into its values
                for key, value in node.items():
                    # Update context if the key looks like a Conversation ID
                    new_context = key if isinstance(value, (list, dict)) else current_context
                    self._recursive_search(value, new_context, results)

        elif isinstance(node, list):
            # 3. If we hit a list, check every item in it
            for item in node:
                self._recursive_search(item, current_context, results)

    def _process_item(self, conv_id, item):
        """
        Standardizes the raw signal into the Black-Tape UI format.
        """
        # Timestamp Extraction (Fallback to Epoch if missing)
        timestamp = next((item.get(k) for k in ["Created", "timestamp", "Timestamp", "time", "date"] if k in item), "1970-01-01 00:00:00")

        # Sender Identity Logic
        is_sender_flag = None
        for flag in ["is_sender", "IsSender", "FromMe", "isSender"]:
            if flag in item:
                is_sender_flag = bool(item[flag])
                break

        return {
            "conversation": str(conv_id),
            "sender": str(item.get("From") or item.get("sender") or "Unknown"),
            "text": str(item.get("Content") or item.get("text") or f"[{item.get('Media Type', 'DATA_SIGNAL')}]"),
            "timestamp": timestamp,
            "is_sender_flag": is_sender_flag,
            "metadata": item # Preserve full original signal for the UI inspection
        }
