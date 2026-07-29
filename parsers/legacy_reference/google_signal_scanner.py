from __future__ import annotations


class GoogleSignalScanner:
    """Extracts non-coordinate Google timeline signals for Timeline and Analytics views."""

    def scan(self, filename: str, data) -> list[dict]:
        lowered = filename.lower()
        if "timeline edits" not in lowered and "timeline_edits" not in lowered:
            return []
        if not isinstance(data, dict):
            return []

        events: list[dict] = []
        for index, edit in enumerate(data.get("timelineEdits") or data.get("Timeline Edits") or []):
            if not isinstance(edit, dict):
                continue
            raw_signal = edit.get("rawSignal") or {}
            signal = raw_signal.get("signal") or {}
            metadata = raw_signal.get("metadata") or {}
            additional_timestamp = str(raw_signal.get("additionalTimestamp") or "").strip()
            device_id = str(edit.get("deviceId") or "").strip()
            platform = str(metadata.get("platform") or "").strip()

            activity_record = signal.get("activityRecord") or {}
            activities = [item for item in (activity_record.get("detectedActivities") or []) if isinstance(item, dict)]
            if activities:
                best_activity = max(activities, key=lambda item: float(item.get("probability") or 0))
                events.append(
                    {
                        "id": f"google-activity:{index}",
                        "timestamp": str(activity_record.get("timestamp") or additional_timestamp).strip(),
                        "kind": "google",
                        "subkind": "activity",
                        "source": filename,
                        "summary": f"{best_activity.get('activityType', 'UNKNOWN')} activity detected",
                        "details": {
                            "device_id": device_id or "unknown",
                            "platform": platform or "unknown",
                            "activity_type": str(best_activity.get("activityType") or "UNKNOWN"),
                            "activity_confidence": float(best_activity.get("probability") or 0),
                            "additional_timestamp": additional_timestamp or "unknown",
                        },
                    }
                )

            wifi_scan = signal.get("wifiScan") or {}
            devices = [item for item in (wifi_scan.get("devices") or []) if isinstance(item, dict)]
            if devices:
                strongest = max(
                    (device.get("rawRssi") for device in devices if device.get("rawRssi") is not None),
                    default=None,
                )
                events.append(
                    {
                        "id": f"google-wifi:{index}",
                        "timestamp": str(wifi_scan.get("deliveryTime") or additional_timestamp).strip(),
                        "kind": "google",
                        "subkind": "wifi_scan",
                        "source": filename,
                        "summary": f"Wi-Fi scan captured {len(devices)} devices",
                        "details": {
                            "device_id": device_id or "unknown",
                            "platform": platform or "unknown",
                            "scan_source": str(wifi_scan.get("source") or "unknown"),
                            "device_count": len(devices),
                            "strongest_rssi": strongest if strongest is not None else "unknown",
                            "additional_timestamp": additional_timestamp or "unknown",
                        },
                    }
                )

        return [event for event in events if event.get("timestamp")]
