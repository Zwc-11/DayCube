"""Batched binocular prediction against independent per-eye calculations, with synthetic inputs."""

import numpy as np
import pytest
from gaze.head import GazeModel, directions
from test_head import SCREEN, _rotation


@pytest.mark.parametrize("frames", [0, 1, 7, 25])
def test_binocular_batch_preserves_each_eye_and_inputs(frames: int) -> None:
    rng = np.random.default_rng(7)
    model = GazeModel(SCREEN)
    model.eyes = [(rng.normal(size=4), rng.uniform(0.5, 2, 4), rng.normal(size=(5, 2))) for _ in range(2)]
    F = rng.normal(size=(frames, 2, 4))
    R = np.array([_rotation(i - 12, i % 7 - 3) for i in range(frames)]).reshape(frames, 3, 3)
    before_F, before_R = F.copy(), R.copy()
    xy = []
    for e, (mean, sd, weights) in enumerate(model.eyes):
        xy.append((F[:, e] - mean) / sd @ weights[1:] + weights[0])
    expected = directions(np.stack(xy, 1)) @ R.transpose(0, 2, 1)
    actual = model.directions_of(R, F)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(F, before_F)
    np.testing.assert_array_equal(R, before_R)
    held = actual.copy()
    model.directions_of(R, F + 1)
    np.testing.assert_array_equal(actual, held)


def test_predict_keeps_one_eye_when_the_other_ray_misses() -> None:
    model = GazeModel(SCREEN)
    model.eyes = [(np.zeros(4), np.ones(4), np.zeros((5, 2))) for _ in range(2)]
    R = np.repeat(np.eye(3)[None], 3, axis=0)
    eyes = np.array(
        [[[30, 150, 600], [-30, 150, 600]], [[30, 150, 600], [-30, 150, -600]], [[30, 150, -600], [-30, 150, -600]]],
        dtype=float,
    )
    result = model.predict_arrays(R, eyes, np.zeros((3, 2, 4)))
    per_eye = SCREEN.pixel(eyes, np.broadcast_to([0, 0, -1], eyes.shape))
    np.testing.assert_array_equal(result[0], per_eye[0].mean(0))
    np.testing.assert_array_equal(result[1], per_eye[1, 0])
    assert np.isnan(result[2]).all()
