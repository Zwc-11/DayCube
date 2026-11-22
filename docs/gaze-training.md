# Gaze training benchmark

Which way of learning from clicks makes the webcam eye tracker most accurate? This benchmark
answers on real webcam images, with DayCube's own geometry, in DayCube's situation: a short
calibration, then labels that arrive as clicks do, noisy and in time order.

## Setup

- **Images.** MPIIFaceGaze: 15 people on their own laptops over days, 37,667 images, each taken
  while the person looked at a known point on the screen.
- **Geometry.** DayCube's head pose and eye measurements for every image
  (`eval/gaze_train_prepare.py` calls `gaze.head`), the camera's field of view assumed and the
  screen centred under the camera, as the tracker assumes them.
- **Calibration.** The person's first 20 images, with clean labels.
- **Click labels.** The next N images in time order. Each label is moved by click noise: a
  Gaussian with a 20 mm median, and 12% of labels replaced by a random point on the screen (a
  click made without looking).
- **Test.** The person's last 40% of images, on later days, with their true targets.
- **Score.** On-screen error in millimetres: each person's median, then the mean over people.

Every method predicts each eye's direction in the head, and the same geometry carries it to the
screen, so only what is learned differs.

## Methods

| Method | Reads | Learns |
| --- | --- | --- |
| Calibration only | 4 landmark numbers per eye | A ridge from the 20 calibration images |
| Ridge, Huber weights | The same, plus 6 head-pose terms | A ridge refitted on the click labels with outliers down-weighted; the closest to what `gaze/learn.py` does today |
| Boosting on landmarks | The same | Gradient-boosted trees |
| Eye network, own clicks | A 60×36 grey crop of each eye, plus the same numbers | A small convolutional network (channels 16, 32, 64, 64; 96 hidden units), Huber loss, 300 to 1,000 steps on the CPU |
| Eye network, other people first | The same | The same network trained on 12 other people (1,500 steps), then tuned on this person's clicks |

## Results

On-screen error, mean of 15 people, by how many click labels there are:

| Click labels | Ridge, Huber weights | Eye network, own clicks | Eye network, other people first |
| ---: | ---: | ---: | ---: |
| 0 | 50.2 mm (calibration only) | | 38.7 mm (as trained, not tuned) |
| 100 | 44.3 mm | 38.9 mm | **25.7 mm (2.91°)** |
| 300 | 39.1 mm | 29.8 mm | **24.1 mm (2.71°)** |
| 1,000 | 35.6 mm (4.10°) | 25.1 mm (2.84°) | **21.2 mm (2.40°)** |

- **Reading the eye's pixels beats the landmark numbers at every label count.** With 1,000 labels
  the network trained on a person's own clicks is 29% under the ridge (better for 14 of 15
  people); boosting on the same landmark numbers gains 3%.
- **Training on other people first is the larger gain, and largest when labels are few.** Tuned on
  100 clicks it is 42% under the ridge and about as good as the own-clicks network at 1,000.
  At 1,000 clicks it is 40% under the ridge. It was better than the ridge for all 15 people at
  every label count. Tuning takes 20 to 40 seconds on two CPU cores.
- **Resolution is not the limit.** With every image shrunk to DayCube's 640×480 mode (the eye 22
  pixels wide, against 59 as recorded) the ridge scores 36.0 mm and the own-clicks network
  25.1 mm: the same as at full size.
- **Click noise costs little.** With clean labels the own-clicks network scores 23.3 mm against
  25.1 mm with click noise (8%); the ridge 34.4 mm against 35.6 mm. Cleaning labels harder is
  not where the accuracy is.

The table is in [`eval/results/gaze-train.json`](../eval/results/gaze-train.json).

## The licence of a trained network

MPIIFaceGaze is released under CC BY-NC-SA 4.0, for non-commercial scientific use. Measuring on
it, as here, is that use. A network trained on it carries those terms, so DayCube does not ship
one: the released tracker can train on a person's own clicks (the middle column), and a base
network for the right-hand column has to come from eye pictures whose owners agreed to that use.
Anyone may train the base network for their own research with the commands below.

## Run it

```powershell
# once: landmarks and eye crops for every image (the eye tracker's environment)
services\gaze\.venv\Scripts\python eval\gaze_train_extract.py <MPIIFaceGaze folder> <cache>
services\gaze\.venv\Scripts\python eval\gaze_train_prepare.py <cache> <prepared>
# the comparison (any environment with PyTorch and scikit-learn)
python eval\gaze_train_bench.py <prepared> --stage main --out results.jsonl
python eval\gaze_train_bench.py --report results.jsonl
```

Stages: `main` (1,000 labels, both resolutions), `curve` (100 and 300 labels), `cross` (the
network trained on other people first: run `--pretrain 0 1 2 3 4` before it), `clean` (labels
without click noise). `loss` and `extra` (other losses, eye pictures alone, larger crops) are in
the script but not part of these results.

## Limits

The click noise is simulated, from published figures for filtered click labels. The people sat
at laptops, closer than a desktop monitor. The benchmark scores the learned part only: a wrong
assumed screen position costs the same for every method.
