from pathlib import Path

import pytest
from ingest.mind.attention import Fixation, attention
from ingest.skeleton.parsers import parse_gpx


def test_attention_clips_dwell_and_merges_overlapping_fixations() -> None:
    fixations = [
        Fixation(50, 50, -1, 3, 1),
        Fixation(50, 50, 1, 2, 1),
        Fixation(300, 300, 0, 5, 1),
        Fixation(50, 50, 3, 1, 0.2),
    ]
    assert attention((0, 0, 100, 100), fixations, (0, 5)) == pytest.approx(0.6)


def test_gpx_parser_handles_namespaces_sorting_and_missing_altitude(tmp_path: Path) -> None:
    path = tmp_path / "export.gpx"
    path.write_text(
        """<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
      <trkpt lat="43.6" lon="-79.3"><time>2026-10-20T12:00:01Z</time></trkpt>
      <trkpt lat="43.7" lon="-79.4"><ele>10</ele><time>2026-10-20T12:00:00Z</time></trkpt>
    </trkseg></trk></gpx>""",
        encoding="utf-8",
    )
    fixes = parse_gpx(path)
    assert fixes[0].alt_abs == 10
    assert fixes[1].alt_abs is None
    assert fixes[0].t < fixes[1].t
