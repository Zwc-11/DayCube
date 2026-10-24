from pathlib import Path
from xml.etree import ElementTree

from ...time import instant
from .types import Fix


def parse_gpx(path: Path) -> list[Fix]:
    fixes = []
    for _, element in ElementTree.iterparse(path, events=("end",)):
        if element.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        time = element.findtext("{*}time")
        elevation = element.findtext("{*}ele")
        if time:
            fixes.append(
                Fix(
                    t=instant(time),
                    lat=float(element.attrib["lat"]),
                    lon=float(element.attrib["lon"]),
                    alt_abs=float(elevation) if elevation else None,
                    source="arc_gpx",
                )
            )
        element.clear()
    return sorted(fixes, key=lambda fix: fix.t)
