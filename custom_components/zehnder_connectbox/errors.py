"""Expected command refusals, independent of Home Assistant."""


class CommandValidationError(ValueError):
    """A command cannot run in the current state or with the supplied values."""

    def __init__(self, translation_key: str) -> None:
        self.translation_key = translation_key
        super().__init__(translation_key)
