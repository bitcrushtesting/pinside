"""Spring-pin probes, and the holes a fixture board needs for them.

A pogo pin is two parts: a receptacle soldered or pressed into the fixture board, and a spring
pin that drops into it. Splitting them is what makes a fixture maintainable -- a bent or worn pin
pulls out and a new one goes in, without touching the board.

What the fixture board actually needs from a probe is four numbers: the hole to drill, the pad to
put round it, how much room the body takes, and how close two of them may sit. Those are the
fields here. The mounting geometry drives both the generated footprint and the spacing check, so
choosing a finer probe automatically relaxes PS021 rather than requiring a second edit.

The dimensions are the values pinside builds to. Check them against your supplier's drawing
before ordering: series get revised, and a receptacle that is 0.1 mm fatter than expected is a
board that has to be redrilled.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Probe:
    """One spring-pin family, as the fixture board sees it."""

    name: str
    description: str
    receptacle: str
    pin: str
    drill_mm: float
    pad_mm: float
    body_dia_mm: float
    min_pitch_mm: float
    travel_mm: float
    force_n: float
    tip: str
    mounting: str = "press-fit"

    @property
    def footprint_name(self) -> str:
        return f"PogoPin_{self.name}"

    def summary(self) -> str:
        return (
            f"{self.receptacle} receptacle + {self.pin} pin, "
            f"{self.drill_mm} mm hole on a {self.min_pitch_mm} mm minimum pitch"
        )


PROBES: dict[str, Probe] = {
    # The workhorse: 0.1 in pitch, forgiving to place, and the pin is a stock item everywhere.
    # Anything laid out on a 2.54 mm grid takes these without thinking about it.
    "millmax_0985": Probe(
        name="millmax_0985",
        description="Mill-Max 0985 receptacle with a 0900 series spring pin, 2.54 mm pitch",
        receptacle="0985-0-15-20-71-14-11-0",
        pin="0900-0-15-20-75-14-11-0",
        drill_mm=1.37,
        pad_mm=2.29,
        body_dia_mm=1.70,
        min_pitch_mm=2.54,
        travel_mm=2.5,
        force_n=0.75,
        tip="crown -- bites through the light oxide on a bare copper or HASL pad",
    ),
    # For a board whose test pads were not laid out with a fixture in mind, and which therefore
    # sit closer together than 2.54 mm.
    "millmax_0906": Probe(
        name="millmax_0906",
        description="Mill-Max 0906 receptacle with a 0850 series spring pin, 1.91 mm pitch",
        receptacle="0906-0-15-20-76-14-11-0",
        pin="0850-0-15-20-82-14-11-0",
        drill_mm=1.02,
        pad_mm=1.70,
        body_dia_mm=1.27,
        min_pitch_mm=1.91,
        travel_mm=1.8,
        force_n=0.55,
        tip="crown",
    ),
    # No receptacle: the pin is soldered straight into the board. Cheaper and lower profile, at
    # the cost of a soldering iron every time a pin wears out.
    "soldered_1mm": Probe(
        name="soldered_1mm",
        description="A 1.0 mm shank spring pin soldered directly into the board, no receptacle",
        receptacle="(none)",
        pin="P75-series or equivalent",
        drill_mm=1.02,
        pad_mm=1.60,
        body_dia_mm=1.00,
        min_pitch_mm=2.00,
        travel_mm=2.0,
        force_n=0.70,
        tip="crown",
        mounting="soldered",
    ),
}

DEFAULT = "millmax_0985"


def get(name: str | None = None) -> Probe:
    key = (name or DEFAULT).lower()
    try:
        return PROBES[key]
    except KeyError:
        known = ", ".join(sorted(PROBES))
        raise ValueError(f"unknown probe {name!r}; known probes: {known}") from None
