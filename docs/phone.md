# Phone recordings

A phone carried through the day records what the computer cannot: where you went, how you got
there, which floor you were on, what was said and what was in front of you. DayCube imports the
exports of three free iPhone apps (Sensor Logger also runs on Android) into the vault day they
were recorded on (`services/ingest/ingest/phone`).

| App | What to export | Becomes |
| --- | --- | --- |
| [Sensor Logger](https://www.tszheichoi.com/sensorlogger) | the recording as a zip of CSVs (Location, Barometer, Pedometer, Activity; others are ignored) | visits, trips, floors, every GPS fix as the day's skeleton |
| Voice Memos | the `.m4a` | an audio moment with its transcript |
| [Stray Scanner](https://docs.strayrobots.io/apps/scanner/) | the capture folder (`rgb.mp4`, `odometry.csv`, `camera_matrix.csv`, and `depth/` when there) | a video moment with keyframes and the camera's path on the map |

## Getting them in

Copy the exports into `vault/inbox/phone` (an iCloud Drive or OneDrive folder synced to the PC
works). While the capture receiver runs, it looks every minute and imports what has stopped
changing for 30 s, then moves it to `inbox/phone/imported/<when>/`. A memo or scan that cannot be
read (FFmpeg not on PATH, say) is left in the inbox while the rest is imported; it and any failed
import are written to `inbox/phone/errors.jsonl` and not tried again until the file changes (copy
it in again once fixed). By hand:

    .\capture import-phone "C:\path\to\export.zip" "C:\path\to\memo.m4a" "C:\path\to\scan folder"
    uv run daycube import-phone <files...> [--no-transcribe] [--inbox]

Importing again replaces exactly what an earlier import wrote: places `ph-HHMMSS`, events `ph_...`,
moments `mo_voice_...` and `mo_scan_...`, skeleton points with source `sensor-logger`. Anything else
in the day (computer capture, edits, other sources) is kept.

## Visits and trips

`visits.py` cuts the recording into stays and trips. The phone's motion activity (Core Motion:
stationary, walking, running, cycling, automotive) says how it moved; GPS alone confuses a drive
in traffic with a walk. The most common known activity over a sliding minute decides; driving or
cycling for two minutes or more is a trip, and a stop shorter than three minutes stays part of
it. Without activity, a minute's median speed over 6 m/s is driving. What lies between trips is a
stay, split where the phone was still for five minutes at two places over 250 m apart (the walk
between becomes a trip on foot).

A stay mostly on foot and wider than 75 m is an outdoor visit; the stay the recording starts and
ends at is home (Sensor Logger names a recording after where it began, and that name is the
place's); any other stay is a building. Floors come from the barometer inside each building visit
(`skeleton/floors.py`, [floors](floors.md)), as `place.floor` events.

GPS inside a building wanders tens of metres, so fixes in a house or building visit carry no
position on the stage (the viewer walks its stage instead); an outdoor visit's fixes are metres east
and north of its middle, and the viewer shrinks a walk wider than the stage to fit it
(`frame.scale`). The skeleton keeps every fix's latitude and longitude.

## Voice memos

**When.** Voice Memos stamps a file with the moment it was saved: its end. A memo recorded
alongside a Sensor Logger recording on 2026-10-24 (18 min 51 s), read so, began on the drive back a
few hundred metres from home, and what was said in its first minutes matched the arrival; read as
its start, it would have begun after the sensor recording had ended, though the memo itself
mentions both recording. A recording app that stamps the start: `--audio-stamped-at-start`.

**What was said.** Transcribed on the CPU with faster-whisper (`small`, int8, word times, voice
activity filtering): `uv sync --extra transcribe` to install it. A transcript beside the memo
(`<memo name>.json`, faster-whisper's segments or the vault's transcript format) is used instead.
Without either, the moment keeps its audio and says it was not transcribed. Transcript segments
are search units, so Ask finds what was said and replays the moment there.

**Others' voices.** Speakers are not told apart (no diarization yet), and the consent workflow for
other people recorded ([consent](consent.md)) is not implemented: an imported memo lists no
consented people. It stays in your vault, on your PC.

## Stray Scanner captures

**When.** The video's `creation_time` is when recording began: on 2026-10-24 the camera's own path
(ARKit) matched the phone's GPS track best with the video starting 1 s after it (speed correlation
0.42, against -0.01 taking it as the end).

**Where.** ARKit's positions are metres in a frame of its own, gravity aligned, so the horizontal
path differs from metres east and north by a rotation and a shift, fitted by least squares
(Kabsch, no scaling) to the GPS fixes over the capture. A start other than the stamped one (within
20 s) is taken only when it fits with under 80% of the residual. The 2026-10-24 capture (2 min 9 s,
a 160 m walk) went onto the map with 2.0 m RMS residual. Every camera position, with its compass
heading, is in `camera.json`; a keyframe every 8 s lets the moment be browsed without the video.

**3D.** A scene needs the capture's `depth/` folder (one PNG per frame) fused with the poses; only
the video and poses have come so far, so the moment says so instead of showing a reconstruction.

## In the viewer

Each visit is a layer; drives are the trips between them. Double-click a voice memo or scan on the
day to open it: the recording itself (audio or video), the transcript (a line seeks the recording
there), the keyframes, and for a scan the camera's path seen from above with where it is and which
way it looks.

## Tests

`services/ingest/tests/test_phone_import.py` builds a synthetic afternoon the way the apps export
it (home, a drive, a walk around a park with a scan turned 30 degrees from north, the drive back
with a memo, upstairs at home) and checks the visits, trips and floor, the memo's start and place,
the scan's rotation and fit, re-importing, the inbox, and the viewer's day and media.
`fixtures/phone-day` (`scripts/build-phone-fixture.py`) is a fictional afternoon the viewer's
end-to-end tests import as 2026-10-21 (`apps/viewer/e2e/phone.spec.ts`).
