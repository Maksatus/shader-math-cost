"""Errors a command stops with: the message says what happened, `code` names it for the UI (what to do about it)."""


class ParetoError(RuntimeError):
    code = "error"

    def __init__(self, message="", code=None):
        super().__init__(message)
        if code:
            self.code = code
