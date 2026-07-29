from __future__ import annotations

from black_tape_engine.legacy_core.base_scanner import BaseScanner


class FriendsScanner(BaseScanner):
    """Extracts friend relationship records from Snapchat-style export files."""

    FRIEND_KEYS = {
        "friends",
        "deleted friends",
        "friend requests sent",
        "blocked users",
        "hidden friend suggestions",
        "ignored snapchatters",
        "pending requests",
        "shortcuts",
    }

    def scan(self, filename: str, data) -> dict:
        lowered_name = filename.lower()
        if not isinstance(data, dict):
            return {}

        payload: dict[str, object] = {}

        if "friends" in lowered_name:
            categories = {}
            for key, value in data.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in self.FRIEND_KEYS and isinstance(value, list):
                    categories[self._slugify(key)] = [self._normalize_friend(item, key) for item in value if isinstance(item, dict)]

            if categories:
                payload["categories"] = categories

        if "ranking" in lowered_name:
            stats = data.get("Statistics")
            if isinstance(stats, dict):
                payload["ranking"] = {
                    "snapscore": self._to_int(stats.get("Snapscore")),
                    "total_friends": self._to_int(stats.get("Your Total Friends")),
                    "following": self._to_int(stats.get("The Number of Accounts You Follow")),
                    "raw": stats,
                }

        return payload

    def _normalize_friend(self, item: dict, category: str) -> dict:
        return {
            "username": str(item.get("Username") or "").strip(),
            "display_name": str(item.get("Display Name") or "").strip(),
            "created": str(item.get("Creation Timestamp") or "").strip(),
            "modified": str(item.get("Last Modified Timestamp") or "").strip(),
            "source": str(item.get("Source") or "").strip(),
            "category": self._slugify(category),
        }

    def _slugify(self, value: str) -> str:
        return (
            str(value)
            .strip()
            .lower()
            .replace("&", "and")
            .replace("/", " ")
            .replace("-", " ")
            .replace(" ", "_")
        )

    def _to_int(self, value) -> int:
        try:
            if value in (None, ""):
                return 0
            return int(float(value))
        except (TypeError, ValueError):
            return 0
