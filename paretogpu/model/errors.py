"""Errors a command stops with: the message says what happened, `code` names it for the UI (what to do about it).

Codes the UI has a hint for (ui/runner.py HINTS): editor_busy, unity_cli_off, no_frame, rd_not_game_frame, renderdoc,
malioc, no_snapshot; the others are shown as they are. The C# scripts in the editor fail with these codes too
(adapters/unity/cs/ParetoGpuJob.cs).
"""


class ParetoError(RuntimeError):
    code = "error"

    def __init__(self, message="", code=None):
        super().__init__(message)
        if code:
            self.code = code
