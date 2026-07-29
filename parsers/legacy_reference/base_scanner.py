class BaseScanner:
    """Base class for all scanners."""

    def scan(self, source):
        raise NotImplementedError("Scan method not implemented.")
