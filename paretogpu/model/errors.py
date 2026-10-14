"""`code` keys the UI hint (ui/runner.py HINTS); the C# editor scripts (ParetoGpuJob.cs) fail with the same codes."""


class ParetoError(RuntimeError):
    code = "error"

    def __init__(self, message="", code=None):
        super().__init__(message)
        if code:
            self.code = code
