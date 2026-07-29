    def get_gps(self, job_id: str) -> list[dict]:
        data = self.get_data(job_id)
        if not data:
            return []
        return data.get("gps", [])

    def get_friends(self, job_id: str) -> dict:
        data = self.get_data(job_id)
        if not data:
            return {"categories": {}, "summary": {}, "ranking": {}}

        friends = data.get("friends") or {}
        categories = friends.get("categories") or {}
        ranking = friends.get("ranking") or {}

        summary = {
            category: len(entries)
            for category, entries in categories.items()
        }
        summary["total_records"] = sum(summary.values())
        summary["unique_usernames"] = len(
            {
                entry.get("username")
                for entries in categories.values()
                for entry in entries
                if entry.get("username")
            }
        )

        return {
            "categories": categories,
            "summary": summary,
            "ranking": ranking,
        }

    def get_timeline(self, job_id: str) -> list[dict]:
        data = self.get_data(job_id)
        if not data:
            return []

        timeline: list[dict] = []

        for convo_id, messages in (data.get("chats") or {}).items():
            for index, message in enumerate(messages):
                timestamp = message.get("Created")
                if not timestamp:
                    continue
                timeline.append(
                    {
                        "id": f"chat:{convo_id}:{index}",
                        "timestamp": timestamp,
                        "kind": "chat",
                        "label": convo_id,
                        "summary": message.get("Content") or "[NO CONTENT]",
                        "details": {
                            "conversation": convo_id,
                            "sender": message.get("SenderName") or "Unknown",
                            "direction": "outbound" if message.get("IsSender") else "inbound",
                        },
                    }
                )

        for index, point in enumerate(data.get("gps") or []):
            timestamp = point.get("timestamp")
            if not timestamp:
                continue
            timeline.append(
                {
                    "id": f"gps:{index}",
                    "timestamp": timestamp,
                    "kind": "gps",
                    "label": point.get("layer") or "gps",
                    "summary": point.get("source") or "GPS point",
                    "details": {
                        "layer": point.get("layer") or "unknown",
                        "source": point.get("source") or "unknown",
                        "coordinates": f"{point.get('lat')}, {point.get('lon')}",
                    },
                }
            )

        for index, signal in enumerate(data.get("google_signals") or []):
            timestamp = signal.get("timestamp")
            if not timestamp:
                continue
            timeline.append(
                {
                    "id": signal.get("id") or f"google:{index}",
                    "timestamp": timestamp,
                    "kind": "google",
                    "label": signal.get("subkind") or "google_signal",
                    "summary": signal.get("summary") or "Google signal",
                    "details": {
                        "source": signal.get("source") or "unknown",
                        **(signal.get("details") or {}),
                    },
                }
            )

        friends = data.get("friends") or {}
        for category, entries in (friends.get("categories") or {}).items():
            for index, entry in enumerate(entries):
                created = entry.get("created")
                modified = entry.get("modified")
                display_name = entry.get("display_name") or entry.get("username") or "Unknown profile"
                if created:
                    timeline.append(
                        {
                            "id": f"friend-created:{category}:{index}",
                            "timestamp": created,
                            "kind": "friend",
                            "label": category,
                            "summary": f"{display_name} added to {category}",
                            "details": {
                                "username": entry.get("username") or "unknown",
                                "display_name": display_name,
                                "bucket": category,
                                "source": entry.get("source") or "unknown",
                                "event": "created",
                            },
                        }
                    )
                if modified and modified != created:
                    timeline.append(
                        {
                            "id": f"friend-modified:{category}:{index}",
                            "timestamp": modified,
                            "kind": "friend",
                            "label": category,
                            "summary": f"{display_name} updated in {category}",
                            "details": {
                                "username": entry.get("username") or "unknown",
                                "display_name": display_name,
                                "bucket": category,
                                "source": entry.get("source") or "unknown",
                                "event": "modified",
                            },
                        }
                    )

        return sorted(timeline, key=lambda item: item["timestamp"])

    def get_analytics(self, job_id: str) -> dict:
        data = self.get_data(job_id)
        if not data:
            return {
                "overview": {},
                "chat": {},
                "gps": {},
                "friends": {},
                "google": {},
            }

        chats = data.get("chats") or {}
        gps_points = data.get("gps") or []
        friends = self.get_friends(job_id)
        google_signals = data.get("google_signals") or []

        total_messages = sum(len(messages) for messages in chats.values())
        top_conversations = sorted(
            (
                {
                    "conversation": convo_id,
                    "messages": len(messages),
                    "last_timestamp": messages[-1].get("Created") if messages else "",
                }
                for convo_id, messages in chats.items()
                if messages
            ),
            key=lambda item: item["messages"],
            reverse=True,
        )[:8]

        gps_layers = Counter(point.get("layer") or "unknown" for point in gps_points)
        day_buckets = Counter((point.get("timestamp") or "").split(" ")[0] for point in gps_points if point.get("timestamp"))
        busiest_days = [
            {"day": day, "points": count}
            for day, count in day_buckets.most_common(8)
            if day
        ]

        google_activity = Counter()
        google_platform = Counter()
        google_signal_types = Counter()
        for signal in google_signals:
            details = signal.get("details") or {}
            if details.get("activity_type"):
                google_activity[str(details["activity_type"])] += 1
            if details.get("platform"):
                google_platform[str(details["platform"])] += 1
            if signal.get("subkind"):
                google_signal_types[str(signal["subkind"])] += 1

        return {
            "overview": {
                "messages": total_messages,
                "conversations": len(chats),
                "gps_points": len(gps_points),
                "friend_records": friends.get("summary", {}).get("total_records", 0),
                "google_signals": len(google_signals),
            },
            "chat": {
                "top_conversations": top_conversations,
            },
            "gps": {
                "layers": dict(gps_layers),
                "busiest_days": busiest_days,
            },
            "friends": {
                "summary": friends.get("summary", {}),
                "ranking": friends.get("ranking", {}),
            },
            "google": {
                "signal_types": dict(google_signal_types),
                "top_activities": [
                    {"activity": name, "count": count}
                    for name, count in google_activity.most_common(6)
                ],
                "platforms": dict(google_platform),
            },
        }

    def get_explore(self, job_id: str) -> dict:
        data = self.get_data(job_id)
        if not data:
            return {"sources": [], "identity": [], "google_signals": [], "other": []}

        identity_payload = data.get("identity") or {}
        sources = []
        identity_markers = identity_payload.get("identity_markers") or {}
        for source_file in identity_payload.get("source_files") or ([identity_payload.get("source_file")] if identity_payload.get("source_file") else []):
            sources.append(
                {
                    "source": source_file,
                    "type": "identity_scan",
                    "metadata_count": identity_payload.get("raw_metadata_count", 0),
                }
            )

        google_signals = []
        for signal in data.get("google_signals") or []:
            details = signal.get("details") or {}
            google_signals.append(
                {
                    "timestamp": signal.get("timestamp") or "",
                    "kind": signal.get("subkind") or "google_signal",
                    "summary": signal.get("summary") or "Google signal",
                    "details": details,
                    "source": signal.get("source") or "",
                }
            )

        other_records = []
        for point in data.get("gps") or []:
            if point.get("layer") == "other":
                other_records.append(
                    {
                        "timestamp": point.get("timestamp") or "",
                        "type": "map_other",
                        "summary": point.get("source") or "Unclassified map point",
                        "details": {
                            "coordinates": f"{point.get('lat')}, {point.get('lon')}",
                            "source_system": point.get("source_system") or "unknown",
                        },
                    }
                )

        return {
            "sources": sources,
            "identity": [{"key": key, "value": value} for key, value in identity_markers.items()],
            "google_signals": google_signals[:80],
            "other": other_records[:80],
        }

    def search(self, job_id: str, query: str) -> list[dict]:
        data = self.get_data(job_id)
        if not data or "chats" not in data:
            return []
        return EngineSearch.execute(data["chats"], query)

    def clear(self, job_id: str) -> None:
        self.cache.delete(f"{job_id}_data")
        self.cache.delete(f"{job_id}_status")
        self._purge_expired()
