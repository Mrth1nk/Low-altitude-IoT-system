class NoOpOpticalGate:
    """Reserved slave optical-gate interface; disabled in the first release."""

    def snapshot(self):
        return {"blocked": False, "state": "CLEAR", "reason": ""}

    def allows_commands(self):
        return True
