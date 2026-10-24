from pathlib import Path

import pytest
from gaze import __main__ as cli


def test_track_cli_defaults_to_the_calibrated_rate_and_accepts_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = []
    monkeypatch.setattr(cli, "do_track", lambda args: seen.append(args.fps))
    monkeypatch.setattr(cli, "do_calibrate", lambda args: seen.append(("calibrate", args.fps)))
    cli.main(["track", "--vault", str(tmp_path)])
    cli.main(["track", "--fps", "15", "--vault", str(tmp_path)])
    cli.main(["calibrate", "--vault", str(tmp_path)])
    # None: the calibrated camera mode's rate (30 without one); calibration asks the webcam for 60.
    assert seen == [None, 15.0, ("calibrate", 60.0)]


@pytest.mark.parametrize("rate", ["0", "-1", "nan", "inf"])
def test_track_cli_rejects_invalid_rate_before_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rate: str
) -> None:
    called = []
    monkeypatch.setattr(cli, "do_track", lambda args: called.append(True))
    with pytest.raises(SystemExit) as error:
        cli.main(["track", "--fps", rate, "--vault", str(tmp_path)])
    assert error.value.code == 2 and not called
