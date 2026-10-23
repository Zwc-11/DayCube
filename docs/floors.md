# Floors from the barometer

Indoors the GPS fix degrades or disappears, but the phone or watch barometer still resolves
height. Air pressure falls about 12 Pa per metre near sea level, so one storey (3–6 m) shows up
as a 0.4–0.7 hPa change. `ingest/skeleton/floors.py` turns a visit's relative altitude into a
floor per sample and `place.floor` events.

## Why it counts movement, not height

Weather moves pressure by up to about 1 hPa an hour, which reads as 8.5 m of climbing. The
spec's first design compared altitude with the level measured at the entrance (spec 3.4). On a
six-hour visit with a 2 hPa weather change, that design reports five floors never climbed;
`test_weather_drift_over_a_long_visit_does_not_climb_floors` reproduces it.

A floor change is fast and weather is slow:

| Motion | Vertical speed |
| --- | ---: |
| Elevator | 1–3 m/s |
| Escalator | about 0.25 m/s |
| Stairs | about 0.2 m/s |
| 5% ramp at walking pace | about 0.07 m/s |
| Fast weather change | about 0.002 m/s |

So the estimator counts altitude change only while the filtered vertical speed exceeds 0.05 m/s:

1. A 15 s running median removes door and elevator pressure spikes.
2. Stretches of movement less than 10 s apart are joined, so a transient that goes up and back
   down nets to zero.
3. Each stretch's net climb is added to a level relative to the entrance.
4. The floor changes only when that level is more than 0.6 storeys from the current floor
   (hysteresis), and then to the nearest floor. A half-level mezzanine does not count as a
   floor.

A day at 1 Hz (57,600 samples) takes about 33 ms.

## Storey height

Floors are `level / storey height`, so the height decides the count. It comes from, in order:

1. the venue pack's `floor_height_m`;
2. OpenStreetMap `height` divided by `building:levels`;
3. the spec's 3.3 m default.

With the default, confidence is capped at 0.75. Malls often have 4.5–6 m storeys, so a mall
without a venue pack or OSM height under-counts: a 5 m storey reads as 1.5 default floors.

## Confidence

Confidence is how close the level sits to a whole floor: 1 at an exact multiple of the storey
height, 0 half a storey away. An Apple Health "flights climbed" sample within 90 s of a change
adds 0.15, capped at 0.98.

## Known limits

- **Entrance level.** Floor numbers count from the floor you entered on, given as `start_floor`.
  A mall entered from parking level P2 needs the venue pack to say where its entrances are.
- **Uneven storeys.** A tall ground floor under shorter upper floors needs per-level elevations
  in the venue pack; one storey height mis-rounds there.
- **Pressurized buildings and elevator shafts.** These can shift pressure by a few pascals
  (under a metre). The movement threshold and hysteresis absorb that, but a slow, steady
  pressurization during a ride is indistinguishable from climbing.
- **Which building.** The barometer gives the floor, not the building; that still comes from the
  last GPS fix and the visit's place.
