"""The indexed assignment must preserve the original full-scan votes and nearest-window ties."""

import numpy as np
import pytest
from speech import speakers


def reference(words: list[dict], times: list[tuple[float, float]], labels: np.ndarray) -> list[int]:
    starts = np.array([a for a, _ in times])
    ends = np.array([b for _, b in times])
    mids = (starts + ends) / 2
    out = []
    for word in words:
        overlap = np.minimum(ends, word["end"]) - np.maximum(starts, word["start"])
        hit = labels[overlap > 0]
        if len(hit):
            out.append(int(np.bincount(hit).argmax()))
        else:
            out.append(int(labels[int(np.argmin(np.abs(mids - (word["start"] + word["end"]) / 2)))]))
    return speakers.smooth(out, words)


@pytest.mark.parametrize("ordered", [True, False])
def test_indexed_votes_match_full_scans_on_changing_windows_and_words(ordered: bool) -> None:
    rng = np.random.default_rng(71)
    for _ in range(80):
        starts = np.cumsum(rng.uniform(0, 2, size=60))
        ends = np.maximum.accumulate(starts + rng.uniform(0.1, 3, size=60))
        times = list(zip(starts.tolist(), ends.tolist(), strict=True))
        labels = rng.integers(0, 6, size=len(times))
        if not ordered:
            order = rng.permutation(len(times))
            times = [times[i] for i in order]
            labels = labels[order]
        at = rng.uniform(-10, ends[-1] + 10, size=150)
        words = [
            {"start": float(a), "end": float(a + d)} for a, d in zip(at, rng.uniform(-0.5, 8, size=150), strict=True)
        ]
        assert speakers.assign(words, [], times, labels) == reference(words, times, labels)


@pytest.mark.parametrize(
    "times,labels,words",
    [
        ([(0.0, 1.0), (4.0, 5.0)], [3, 1], [{"start": 2.5, "end": 2.5}]),  # equidistant
        ([(0.0, 1.0), (0.0, 1.0)], [4, 1], [{"start": 5.0, "end": 5.2}]),  # same midpoint
        ([(0.0, 2.0), (0.0, 2.0)], [4, 1], [{"start": 0.1, "end": 1.9}]),  # tied votes
        ([(0.0, 1.0), (2.0, 3.0)], [4, 1], [{"start": 1.0, "end": 2.0}]),  # touching
        ([(0.0, 0.0), (1.0, 2.0)], [4, 1], [{"start": -0.5, "end": 0.5}]),  # zero window
        ([(0.0, 3.0), (1.0, 2.0)], [4, 1], [{"start": 2.1, "end": 2.2}]),  # nested
        ([(0.0, 1.0)], [2], [{"start": float("nan"), "end": 1.0}]),
        ([(float("nan"), 1.0), (2.0, 3.0)], [4, 1], [{"start": 5.0, "end": 5.2}]),
    ],
)
def test_boundaries_degenerate_inputs_and_ties_keep_the_original_result(
    times: list[tuple[float, float]], labels: list[int], words: list[dict]
) -> None:
    values = np.array(labels)
    assert speakers.assign(words, [], times, values) == reference(words, times, values)


def test_an_empty_word_list_stays_empty_and_missing_windows_still_fail_explicitly() -> None:
    assert speakers.assign([], [], [], np.array([], int)) == []
    with pytest.raises(ValueError, match="argmin"):
        speakers.assign([{"start": 0.0, "end": 1.0}], [], [], np.array([], int))
