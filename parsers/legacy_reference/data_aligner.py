"""
Data Aligner: Universal Schema Normalization
Ensures disparate chat data sources are mapped to a consistent internal schema.
"""

from datetime import datetime

# --- Internal Utility Functions ---

def parse_timestamp(value):
    """
    Attempts to parse a timestamp value into a standardized 'YYYY-MM-DD HH:MM:SS' string.
    Supports datetime objects and various common string formats.
    """
    if not value:
        return "1970-01-01 00:00:00"

    # Already a datetime object
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, str):
        # Sanitize common string suffixes that may interfere with parsing
        value = value.replace(" UTC", "").replace("Z", "").strip()

    # Define supported format patterns for sequential matching
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue

    # Fallback: Return raw string if no patterns match
    return str(value)


def detect_sender(msg):
    """
    Identifies if a message was authored by the primary user.
    Checks multiple possible boolean flags and common naming conventions.
    """
    if "IsSender" in msg:
        return bool(msg["IsSender"])

    if "FromMe" in msg:
        return bool(msg["FromMe"])

    # Fallback to keyword-based detection in sender/author fields
    sender_fields = ["sender", "author"]
    for field in sender_fields:
        if field in msg:
            if str(msg[field]).lower() in ["me", "self", "owner"]:
                return True

    return False


def extract_content(msg):
    """
    Retrieves message body content from multiple possible keys.
    Returns a default placeholder for empty or generic 'text' labels.
    """
    content = (
        msg.get("Content")
        or msg.get("text")
        or msg.get("body")
        or msg.get("message")
        or msg.get("Message")
        or ""
    )
    
    # Handle generic placeholders often found in social media exports
    if content.strip().lower() == "text":
        return "unsaved communication"
        
    return content


def extract_timestamp(msg):
    """
    Retrieves and normalizes the timestamp from multiple possible keys.
    """
    timestamp_raw = (
        msg.get("Created")
        or msg.get("timestamp")
        or msg.get("time")
        or msg.get("date")
        or msg.get("DateTime")
    )
    return parse_timestamp(timestamp_raw)


# --- Primary Entry Point ---

def align_chat_data(raw_data):
    """
    Normalizes incoming chat data into the standard internal structure:

    {
        "Conversation ID": [
            {
                "Created": "YYYY-MM-DD HH:MM:SS",
                "Content": "message text",
                "IsSender": boolean
            },
            ...
        ]
    }
    """

    aligned = {}

    # Case 1: Data is structured as a dictionary of conversations
    if isinstance(raw_data, dict):
        for conv_id, messages in raw_data.items():
            # Handle nested structures (e.g., grouped by sender sub-keys)
            if isinstance(messages, dict):
                messages = [msg for sub_list in messages.values() for msg in sub_list if isinstance(sub_list, list)]

            if not isinstance(messages, list):
                continue

            aligned_messages = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue

                aligned_messages.append({
                    "Created": extract_timestamp(msg),
                    "Content": extract_content(msg),
                    "IsSender": detect_sender(msg)
                })

            # Ensure chronological order per conversation
            aligned_messages.sort(key=lambda x: x["Created"])
            aligned[conv_id] = aligned_messages

    # Case 2: Data is a flat list (single implied conversation)
    elif isinstance(raw_data, list):
        aligned_messages = []
        for msg in raw_data:
            if not isinstance(msg, dict):
                continue

            aligned_messages.append({
                "Created": extract_timestamp(msg),
                "Content": extract_content(msg),
                "IsSender": detect_sender(msg)
            })

        aligned_messages.sort(key=lambda x: x["Created"])
        aligned["Primary Stream"] = aligned_messages

    return aligned
