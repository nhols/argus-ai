from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, field_validator


class Color(Enum):
    RED = (0, 0, 255)
    BLUE = (255, 0, 0)
    GREEN = (0, 180, 0)
    YELLOW = (0, 220, 220)
    WHITE = (255, 255, 255)
    ORANGE = (0, 165, 255)

    @classmethod
    def from_string(cls, s: str) -> Self:
        try:
            return cls[s.upper()]
        except KeyError:
            raise ValueError(f"Invalid color name: {s}")


class ZoneDefinition(BaseModel):
    label: str
    color: Color = Color.RED
    polygon: list[tuple[float, float]]

    @field_validator("color", mode="before")
    def colour_from_string(cls, color_name: Any) -> Any:
        if isinstance(color_name, str):
            return Color.from_string(color_name)
        if isinstance(color_name, (list, tuple)):
            return Color(tuple(color_name))
        return color_name
