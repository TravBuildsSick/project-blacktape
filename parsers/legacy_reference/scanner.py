"""
Generic Scanner: Identity & Metadata Siphon
Recursively extracts identity markers and system signals from unclassified JSON.
"""

import logging
from black_tape_engine.legacy_core.base_scanner import BaseScanner

logger = logging.getLogger("BLACK-TAPE.SCANNER.GENERIC")

class GenericScanner(BaseScanner):
    """
    Acts as the 'Catch-All' for metadata that Chat and GPS scanners might ignore.
    """

    def scan(self, filename, data):
        """
        Scans for common identity and account-level signals.
        """
        # Ignore large binary-like structures
        if not isinstance(data, (dict, list)):
            return {}

        results = {
            "source_file": filename,
            "identity_markers": {},
            "raw_metadata_count": 0
        }

        # 1. Identity Hunt (Targeting Profile Info)
        identity_keys = [
            "username", "screen_name", "display_name",
            "email", "phone_number", "ip_address",
            "user_id", "account_id", "registration_date"
        ]

        def crawl(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    # Check if the key matches our identity interests
                    if any(ik in k.lower() for ik in identity_keys):
                        if isinstance(v, (str, int, float)):
                            results["identity_markers"][k] = v

                    # Count keys for the dashboard 'Data Density' bar
                    results["raw_metadata_count"] += 1
                    crawl(v)
            elif isinstance(obj, list):
                for item in obj:
                    crawl(item)

        crawl(data)

        logger.info(f"[Generic Siphon] Identified {len(results['identity_markers'])} markers in {filename}")
        return results
