"""
GPS Scanner: Geospatial Signal Extraction
Heuristically identifies coordinate pairs and timestamps across heterogeneous JSON.
Optimized for Project Black-Tape Map UI.
"""

import logging
import re

from black_tape_engine.legacy_core.base_scanner import BaseScanner

logger = logging.getLogger("BLACK-TAPE.SCANNER.GPS")


class GPSScanner(BaseScanner):
    NUM_PATTERN = r"(-?\d+\.?\d*)"

    def _detect_source_system(self, filename, data):
        lowered = filename.lower()
        if "timeline edits" in lowered or "timeline_edits" in lowered:
            return "google"
        if "takeout" in lowered:
            return "google"
        if "mydata" in lowered:
            return "snapchat"
        if "snapchat/" in lowered or lowered.startswith("snapchat"):
            return "snapchat"
        if "google/" in lowered or lowered.startswith("google"):
            return "google"
        if not isinstance(data, dict):
            return "unknown"

        keys = {str(key).strip() for key in data.keys()}
        google_markers = {
            "Frequent Locations",
            "Latest Location",
            "Home & Work",
            "Timeline Edits",
            "timelineEdits",
            "Areas you may have visited in the last two years",
        }
        snapchat_markers = {
            "Memories History",
            "Home, School & Work",
        }

        if keys & google_markers:
            return "google"
        if keys & snapchat_markers:
            return "snapchat"
        return "unknown"

    def _classify_layer(self, filename, data):
        lowered = filename.lower()
        source_system = self._detect_source_system(filename, data)
        if source_system == "google":
            if "timeline edits" in lowered or "timeline_edits" in lowered or (isinstance(data, dict) and "timelineEdits" in data):
                return "google_timeline_edits"
            return "google_location_history"
        if "memories" in lowered or (isinstance(data, dict) and "Memories History" in data):
            return "memories_history"
        if "location_history" in lowered or "location" in lowered or (isinstance(data, dict) and "Location History" in data):
            return "location_history"
        return "other"

    def _normalize_timestamp(self, value, fallback="1970-01-01 00:00:00 UTC"):
        if not value:
            return fallback
        text = str(value).strip()
        if not text:
            return fallback
        if "UTC" in text or text.endswith("Z"):
            return text
        return f"{text} UTC"

    def _to_float(self, value):
        try:
            return float(re.search(self.NUM_PATTERN, str(value)).group(1))
        except (ValueError, AttributeError):
            return None

    def _append_point(self, points, filename, layer, source_system, timestamp, lat, lon, **metadata):
        if lat is None or lon is None:
            return
        points.append(
            {
                "timestamp": self._normalize_timestamp(timestamp),
                "lat": lat,
                "lon": lon,
                "source": filename,
                "layer": layer,
                "source_system": source_system,
                **metadata,
            }
        )

    def _scan_google_timeline_edits(self, filename, data):
        points = []
        edits = data.get("timelineEdits") or data.get("Timeline Edits") or []
        context_by_additional_timestamp = {}

        for edit in edits:
            if not isinstance(edit, dict):
                continue
            raw_signal = edit.get("rawSignal") or {}
            signal = raw_signal.get("signal") or {}
            additional_timestamp = raw_signal.get("additionalTimestamp") or ""
            metadata = raw_signal.get("metadata") or {}
            context = context_by_additional_timestamp.setdefault(additional_timestamp, {})
            if metadata.get("platform"):
                context["platform"] = metadata.get("platform")
            if edit.get("deviceId"):
                context["device_id"] = str(edit.get("deviceId"))

            activity_record = signal.get("activityRecord") or {}
            activities = activity_record.get("detectedActivities") or []
            if activities:
                best = max(
                    (
                        {
                            "activity_type": str(item.get("activityType") or "UNKNOWN"),
                            "activity_confidence": float(item.get("probability") or 0),
                        }
                        for item in activities
                        if isinstance(item, dict)
                    ),
                    key=lambda item: item["activity_confidence"],
                    default=None,
                )
                if best:
                    context.update(best)

            wifi_scan = signal.get("wifiScan") or {}
            devices = wifi_scan.get("devices") or []
            if devices:
                context["wifi_scan_count"] = len(devices)
                context["wifi_scan_source"] = str(wifi_scan.get("source") or "")

        for edit in edits:
            if not isinstance(edit, dict):
                continue
            raw_signal = edit.get("rawSignal") or {}
            signal = raw_signal.get("signal") or {}
            additional_timestamp = raw_signal.get("additionalTimestamp") or ""
            position = signal.get("position") or {}
            point = position.get("point") or {}
            lat = self._to_float(point.get("latE7")) / 1e7 if point.get("latE7") is not None else None
            lon = self._to_float(point.get("lngE7")) / 1e7 if point.get("lngE7") is not None else None
            self._append_point(
                points,
                filename,
                "google_timeline_edits",
                "google",
                position.get("timestamp") or additional_timestamp,
                lat,
                lon,
                signal_type="position",
                position_source=str(position.get("source") or ""),
                accuracy_m=(float(position["accuracyMm"]) / 1000.0) if position.get("accuracyMm") is not None else None,
                altitude_m=position.get("altitudeMeters"),
                **context_by_additional_timestamp.get(additional_timestamp, {}),
            )
        return points

    def scan(self, filename, data):
        """
        Specialized GPS Signal Extractor.
        Recursively hunts for lat/lon pairs in location, media, and vault history.
        """
        points = []
        layer = self._classify_layer(filename, data)
        source_system = self._detect_source_system(filename, data)

        if layer == "google_timeline_edits" and isinstance(data, dict):
            points.extend(self._scan_google_timeline_edits(filename, data))
            logger.info(f"[GPS Siphon] Extracted {len(points)} geospatial markers from {filename}")
            return points

        def hunt(obj, current_ts="1970-01-01 00:00:00 UTC"):
            if isinstance(obj, dict):
                potential_ts = obj.get("Created") or obj.get("Date") or obj.get("timestamp") or obj.get("time")
                if potential_ts:
                    current_ts = self._normalize_timestamp(potential_ts, current_ts)

                lat = obj.get("lat") or obj.get("latitude") or obj.get("Latitude")
                lon = obj.get("lon") or obj.get("longitude") or obj.get("Longitude")
                loc_str = obj.get("Location") or obj.get("Coordinates") or obj.get("coords")

                if lat is not None and lon is not None:
                    self._append_point(
                        points,
                        filename,
                        layer,
                        source_system,
                        current_ts,
                        self._to_float(lat),
                        self._to_float(lon),
                    )
                elif loc_str and isinstance(loc_str, str):
                    matches = re.findall(self.NUM_PATTERN, loc_str)
                    if len(matches) >= 2:
                        self._append_point(
                            points,
                            filename,
                            layer,
                            source_system,
                            current_ts,
                            float(matches[-2]),
                            float(matches[-1]),
                        )

                for value in obj.values():
                    hunt(value, current_ts)

            elif isinstance(obj, list):
                for item in obj:
                    hunt(item, current_ts)

        hunt(data)

        if not points and isinstance(data, dict) and "Location History" in data:
            history = data.get("Location History", [])
            for entry in history:
                if isinstance(entry, list) and len(entry) >= 2:
                    ts, coords = entry
                    parts = str(coords).split(",")
                    if len(parts) < 2:
                        continue
                    lat = self._to_float(parts[0])
                    lon = self._to_float(parts[1])
                    self._append_point(
                        points,
                        filename,
                        layer,
                        source_system,
                        ts,
                        lat,
                        lon,
                        signal_type="history_point",
                    )

        logger.info(f"[GPS Siphon] Extracted {len(points)} geospatial markers from {filename}")
        return points
