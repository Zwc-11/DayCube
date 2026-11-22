from pathlib import Path

from ingest.skeleton.parsers import parse_gpx


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
