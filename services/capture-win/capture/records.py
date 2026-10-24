"""What the capture service accepts: browser records from the extension and fixations from the
eye tracker. Every record carries a client-made ``id``, so a batch resent after a lost
acknowledgement is stored once."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# The tracker and the extension are updated with the receiver, but an autostarted receiver keeps
# running old code until the next sign-in while it restarts the tracker from the new code (after a
# recalibration, say). The two records that grow with new versions take fields they do not know
# and ignore them, instead of refusing the whole record.
TOLERANT = ConfigDict(extra="ignore", allow_inf_nan=False)


class Record(Strict):
    id: str = Field(min_length=8, max_length=64)
    t: AwareDatetime


class Tab(Strict):
    tab: int
    window: int
    url: str = Field(max_length=4096)
    title: str = Field(default="", max_length=1000)


class Page(Record, Tab):
    """A navigation committed in a tab's top frame, with how it was reached."""

    kind: Literal["page"]
    transition: str = Field(max_length=40)  # link, typed, reload, auto_bookmark, form_submit, ...
    qualifiers: list[str] = Field(default_factory=list, max_length=10)  # forward_back, from_address_bar, ...


class Focus(Record):
    """The tab in front changed. ``tab`` is None when the browser lost focus; ``url`` is None
    when the tab in front is excluded."""

    kind: Literal["focus"]
    tab: int | None = None
    window: int | None = None
    url: str | None = Field(default=None, max_length=4096)
    title: str | None = Field(default=None, max_length=1000)


class Title(Record, Tab):
    kind: Literal["title"]


class Idle(Record):
    kind: Literal["idle"]
    state: Literal["active", "idle", "locked"]


class Search(Record, Tab):
    kind: Literal["search"]
    engine: str = Field(max_length=40)
    query: str = Field(max_length=1000)


class Selection(Record, Tab):
    kind: Literal["selection"]
    text: str = Field(max_length=2000)
    copied: bool = False


class Media(Record, Tab):
    kind: Literal["media"]
    state: Literal["playing", "paused", "ended"]
    position_s: float = Field(ge=0)
    duration_s: float | None = Field(default=None, ge=0)


class Geometry(Strict):
    """Where the page's viewport is on its monitor.

    Screen values are DIP (Windows logical pixels) relative to the monitor's top-left corner;
    page values are CSS pixels; ``scale`` is DIP per CSS pixel (the page zoom). ``exact`` is
    true when the viewport origin was measured from a mouse event rather than estimated from
    window sizes."""

    vx: float
    vy: float
    scale: float = Field(gt=0, le=10)
    inner_w: float = Field(gt=0)
    inner_h: float = Field(gt=0)
    screen_w: float = Field(gt=0)
    screen_h: float = Field(gt=0)
    primary: bool  # on the primary monitor, the one the eye tracker is calibrated on
    exact: bool
    scroll_y: float = Field(ge=0)
    scroll_h: float = Field(ge=0)


class Click(Record, Tab):
    """A click on a link or button. ``x`` and ``y`` (CSS px in the viewport) and the viewport's
    ``geometry`` at that moment make it a calibration point for the eye tracker (anchors.py)."""

    model_config = TOLERANT
    kind: Literal["click"]
    href: str | None = Field(default=None, max_length=4096)
    text: str = Field(default="", max_length=300)
    x: float | None = Field(default=None, ge=-100_000, le=100_000)
    y: float | None = Field(default=None, ge=-100_000, le=100_000)
    geometry: Geometry | None = None


class Line(Strict):
    """One visual line of a block: its rectangle in page coordinates (CSS px, scroll included) and
    the text on it."""

    rect: Annotated[list[float], Field(min_length=4, max_length=4)]  # left, top, width, height
    text: str = Field(default="", max_length=300)


class Block(Strict):
    """A visible run of text: heading, paragraph, list item, cell. ``text`` (cut to 1,000
    characters) and ``words`` (counted before the cut) are sent the first time a document shows
    the block or when it changes; later snapshots carry only its id and position. ``lines`` come
    with the text, and again when its wrapping changes (width, zoom) or it moves on the page."""

    model_config = TOLERANT
    id: str = Field(min_length=1, max_length=16)
    tag: str = Field(max_length=16)
    text: str | None = Field(default=None, max_length=1000)
    words: int | None = Field(default=None, ge=0)
    rect: Annotated[list[float], Field(min_length=4, max_length=4)]  # left, top, width, height in CSS px
    lines: list[Line] | None = Field(default=None, max_length=60)


class Blocks(Record, Tab):
    """The text on screen. ``doc`` names one load of a page, and block ids are unique within it."""

    kind: Literal["blocks"]
    doc: str = Field(min_length=4, max_length=40)
    geometry: Geometry
    blocks: list[Block] = Field(max_length=150)


class View(Record, Tab):
    """A heartbeat from a page on screen: used live to place gaze, never stored. ``hidden``: the
    page just left the screen (another tab, minimized, covered by another window)."""

    model_config = TOLERANT
    kind: Literal["view"]
    doc: str = Field(min_length=4, max_length=40)
    geometry: Geometry
    hidden: bool = False


BrowserRecord = Annotated[
    Page | Focus | Title | Idle | Search | Click | Selection | Media | Blocks | View,
    Field(discriminator="kind"),
]


class Fixation(Strict):
    """A fixation from the eye tracker. ``x`` and ``y`` are fractions of the calibrated
    (primary) monitor, so they do not depend on display scaling."""

    id: str = Field(min_length=8, max_length=64)
    t: AwareDatetime
    t_end: AwareDatetime
    x: float = Field(ge=-0.5, le=1.5)
    y: float = Field(ge=-0.5, le=1.5)
    samples: int = Field(ge=1)


class GazeState(Strict):
    model_config = TOLERANT
    tracking: bool
    reason: str | None = Field(default=None, max_length=80)  # why not tracking: paused, away, camera busy, ...
    calibrated: bool = False
    error_px: float | None = Field(default=None, ge=0)
    monitor: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=2, max_length=2)] | None = None  # pixels
    fps: float | None = None
    target_fps: float | None = Field(default=None, gt=0)
    face: float | None = Field(default=None, ge=0, le=1)  # share of recent frames with a usable face
    coverage: float | None = Field(default=None, ge=0, le=1)  # share of recent gaze samples inside fixations
    noise_px: float | None = Field(default=None, ge=0)  # scatter of the smoothed gaze, monitor pixels
    model: str | None = Field(default=None, max_length=40)  # "head" (follows head movement) or an EyeTrax model
    head_moved_mm: float | None = Field(default=None, ge=0)  # head position against calibration, last 10 s
    head_turned_deg: float | None = Field(default=None, ge=0, le=180)
    # Median ms per frame by stage over the last 10 s: camera_wait, landmarks, model, tracking,
    # network, frame (all processing), live_post (a live point's round trip, on its own thread).
    timing_ms: dict[str, Annotated[float, Field(ge=0)]] | None = Field(default=None, max_length=12)


class LivePoint(Strict):
    """The tracker's smoothed gaze point now, for the extension's gaze ring: shown, never stored."""

    t: AwareDatetime
    x: float = Field(ge=-0.1, le=1.1)  # fractions of the monitor
    y: float = Field(ge=-0.1, le=1.1)


class GazeBatch(Strict):
    state: GazeState
    fixations: list[Fixation] = Field(default_factory=list, max_length=1000)
