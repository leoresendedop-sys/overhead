"""Structured errors shared by the engine and the tool handlers."""


class OverheadError(Exception):
    type = "error"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class BadInput(OverheadError):
    type = "bad_input"


class PlaceNotFound(OverheadError):
    type = "place_not_found"


class AmbiguousPlace(OverheadError):
    type = "ambiguous_place"


class AreaTooLarge(OverheadError):
    type = "area_too_large"


class NoClearScene(OverheadError):
    type = "no_clear_scene"


class NetworkError(OverheadError):
    type = "network"


def to_error_dict(e: OverheadError) -> dict:
    return {"error": {"type": e.type, "message": e.message, **e.extra}}
