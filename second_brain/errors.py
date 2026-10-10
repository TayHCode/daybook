# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Fixed actionable diagnostics without transcript, path or subprocess content."""
class UserError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.public_message = message
        self.code = code


def explanation(exc):
    if isinstance(exc, UserError):
        return exc.public_message
    if isinstance(exc, FileNotFoundError):
        return 'A required file or executable is unavailable; check the selected config and tool installation.'
    if isinstance(exc, PermissionError):
        return 'A selected input or output is inaccessible; check its permissions.'
    if isinstance(exc, KeyboardInterrupt):
        return 'The command was interrupted.'
    if isinstance(exc, ValueError):
        return 'Configuration or returned data is invalid; check the selected config and command options.'
    return 'The operation failed; check the reported stage and selected inputs. Raw error content is withheld.'
