# Worlds from videos

A video becomes a world you can walk through. Each part the video saw becomes an *island* of its
own Gaussian splats, placed where it was filmed. Around the islands stands the open map
(OpenStreetMap buildings, kept in the vault). With a Google Maps key, the viewer also streams
Google's Photorealistic 3D Tiles behind everything, live and never stored. The plan and its
reasons are in the project doc `video-to-world-plan.md`.

## What is kept, and where

| File | What |
| --- | --- |
| `vault/<date>/world/<wid>/world.json` | The world: its origin on the Earth and its islands (below) |
| `vault/<date>/world/<wid>/<island>.splat` | An island's splats: the viewer's 32-byte records, in the island's own metres |
| `vault/maps/osm/<sha256>.json` | Overpass answers, one per rounded location and radius (ODbL) |
| `vault/maps/egm96_15.tif` | The EGM96 geoid on a 15′ grid (2.7 MB, public domain, NGA via cdn.proj.org) |

`world.json` (`ingest/world/store.py` validates it on every save):

```json
{"id": "wd_<12 hex>", "date": "2026-11-05", "title": "…", "made": "ISO time",
 "source": {"file": "…", "t0_s": 100.0, "t1_s": 122.0},
 "origin": {"lat": 43.6447, "lon": -79.3801, "ground_m": 52.7},
 "islands": [{"id": "is_01", "splat": "is_01.splat", "points": 412345,
              "enu": [[…4 x 4 row-major…]],
              "bounds": {"min": [x, y, z], "max": [x, y, z]},
              "located_by": "gps | landmark | landmark+footprints | manual", "error_m": 4.0,
              "kind": "seen | imagined", "shots": [[100.0, 108.5]]}]}
```

The world's frame:
- **Axes:** three.js axes in metres. x is east, y is up and z is south, with the origin on the ground at `origin.lat, origin.lon`. Use `ingest/world/geo.py`, `local_from_lat_lon` and its inverse.
- **Accuracy:** it is a tangent plane with the WGS84 radii of curvature at the origin, good to well under 0.1% within 2 km.
- **`ground_m`:** the ground's height on the WGS84 ellipsoid, which is the height 3D Tiles use. The viewer stands the frame there.
- **`enu`:** takes an island's own metres into this frame.
- **`bounds`:** the island's box in this frame. The viewer hides Google's tiles inside it, so your own footage wins where it exists.

## API

| Method | Route | Answer |
| --- | --- | --- |
| GET | `/worlds` | `[{date, id, title, islands, origin}]`, newest first (day, then when made) |
| GET | `/worlds/{date}/{wid}` | `world.json` as stored |
| GET | `/worlds/{date}/{wid}/{file}` | An island's splat; only a name that an island of that world lists; `application/octet-stream` |
| GET | `/maps/buildings?lat=&lon=&radius_m=` | OpenStreetMap footprints with heights (radius 50–600 m, default 300) as GeoJSON with an `attribution`. `503` when OSM cannot be reached and the area is not kept yet |
| GET | `/maps/config` | Vault token required. `{google_tiles_key}` from `DAYCUBE_GOOGLE_MAPS_KEY`; `null` without one |

How `/maps/buildings` works:
- **What it fetches:** ways and multipolygon relations tagged `building`, with their geometry.
  - A footprint is closed if open.
  - A relation gives one polygon per closed outer way. An outer split over several ways is left out rather than stitched wrongly.
- **Heights:**
  - `height_m` comes from `height` (metres, or feet as `ft` or `'`), else `building:levels × 3.2 m`, else 10 m. `height_from` says which.
  - `min_height_m` comes from `min_height`, else `building:min_level × 3.2 m`, else 0.
- **What leaves the PC:** the location is rounded to 3 decimals (about 110 m) before it is sent to Overpass, once per area. After that the vault's copy answers, offline too.
- **User-Agent:** Overpass answers 406 to a request without one, so the adapter always sends `DAYCUBE/0.1 local-development`.

## Google's tiles are a live layer, never data

From the Map Tiles API policies (https://developers.google.com/maps/documentation/tile/policies),
read 2026-11-08:

- Allowed: streaming the tiles in your own renderer, with Google's logo and the data attributions on screen.
- Allowed: overlaying your own 3D objects "as long as the 3D objects aren't extracted, traced, or otherwise derived by hand or machine from Photorealistic 3D Tiles". Google's own example is tiles "as background filler" with your own photorealistic models in the foreground.
- Not allowed: pre-fetching, storing or caching tiles beyond the HTTP cache headers, or offline use.
- Not allowed: "image analysis, machine interpretation, object detection/identification, geodata extraction", or programmatically reading heights, distances or elevations from the tiles.

So the backend never fetches, stores or computes with Google content. Its only part is to hand the
user's key to the viewer (`/maps/config`, behind the vault token). Islands are placed, and the
ground's height found, from the video, GPS and open data alone.

## The ground's height

`geo.ground_height(lat, lon, cache)` adds two numbers:
- **Elevation** (`geo.elevation`): inside Canada, the bare ground from Natural Resources Canada's
  CDEM (`geogratis.gc.ca`, Open Government Licence – Canada); elsewhere, or if it does not answer,
  Copernicus DEM GLO-90 through Open-Meteo (CC BY 4.0). Either sends the location to that service.
- **Undulation:** the EGM96 geoid's undulation, read bilinearly from the cached grid, whose samples are points (GeoTIFF PixelIsPoint), with columns wrapping at 180°.

The grid needs Pillow (the `moments` extra), so it is needed only where islands are made.

GLO-90 is a surface model: in a dense downtown it reads rooftops. Measured 2026-11-08:

| Where | GLO-90 | CDEM (bare ground) | CDSM (surface) |
| --- | ---: | ---: | ---: |
| Union Station's footprint centre (43.6447, −79.3801) | 90.0 m | | |
| Front St at York St (43.64523, −79.38193) | 83.0 m | 81.0 m | 95.7 m |

With the EGM96 undulation there (−37.27 m), the Nov 5 world's ground is 81.0 − 37.27 = 43.73 m
above the ellipsoid. A GPS altitude from the phone should override it when there is one.

## Measured

On 2026-11-08 from the cloud workspace, OpenStreetMap within 300 m of Union Station took 34 s for
Overpass to answer (94 KB). It gave 62 footprints:

| Height from | Footprints |
| --- | ---: |
| `height` tag | 20 |
| `building:levels` | 31 |
| Default 10 m | 11 |

Union Station itself has no height tag, so it stands at the default 10 m. The adapter waits up to
90 s; the first view of a new area waits for it, later ones read the vault.

## Islands

`ingest/recon/island.py` (services/recon's environment) makes a world from a shot of a video:

    uv run --project services/recon python -m ingest.recon.island VIDEO --date D --shot T0 T1 \
        (--at LANDMARK [--street NAME] [--pick N] | --wall LAT,LON LAT,LON) [--along M] [--speed 1.3] [--title T]

Where it was filmed is either a landmark (`--at "Union Station, Toronto"`, see "Finding the wall")
or the footprint wall itself (`--wall`, from the corner the walk starts at; OSM, ODbL). `--along`
is how far from that corner the shot starts; with `--at` it may be left out. Version 1 lifts one
frame:

1. **Keyframe:** the sharpest frame of the shot (variance of the Laplacian) with the least of it masked.
2. **Out:** people, vehicles and bicycles (DeepLabV3 MobileNet, torchvision, BSD), widened by a 35th of the frame's width and carried down a 25th of its height (`hole`): the segmenter misses feet, shoes and shadows, and whatever is left of a person grows back in the fill. Nobody passing by is kept in the vault's 3D.
3. **Depth:** Depth Anything V2 Metric Outdoor Small (Apache-2.0). Its metres were 2–3x long at dusk, so two rulers in the frame set the scale, and their median is used:
   - people standing whole in the frame are 1.70 m tall;
   - the camera is 1.45 m above the ground under it (RANSAC plane).
4. **Upright:** the ground's normal is up; the view's forward along the ground is −z.
5. **The wall:** the largest vertical plane beside the camera, 3–15 m up, is laid on the footprint wall (its normal onto the wall's outward side). The camera stands `along + speed × (keyframe − T0)` metres from the corner. Where the facade was seen, from and to how far ahead (`facade_span`), goes to the wall finder.
6. **What one view holds up:** within 35 m of the camera, no blue sky above 5 m, nothing above 15 m, and on the far side of the camera only the street (below 4 m). Towers hundreds of metres away come out of one view flattened onto a sheet 20–40 m out; the map draws them.
7. **Imagined:** where people and vehicles were, kept as a second island of kind `imagined`; the viewer labels it and its switch hides it.
   - Colour: LaMa (Apache-2.0), OpenCV's model-zoo export (`opencv/inpainting_lama`, 93 MB, pinned), run by OpenCV 5's DNN at its fixed 512 × 512 (`fill`). No new runtime.
   - Depth (`depth_behind`): the depth model reads the filled frame, and that reading is multiplied by its ratio to the first reading, carried into the hole from its edge (Navier–Stokes), so the two meet at the edge. The model reads the whole frame about 10% differently once people are gone, so neither reading alone fits.
8. **View:** the seen island carries `view: {eye, look}` (world frame), where the frame was filmed from. The world opens there, as a memory does.

`world.json`'s islands may carry `view`; `store.validate` checks it is two points.

### Finding the wall

`--at` names the building the shot was filmed beside (`ingest/world/walls.py`). Before the models
run, so that a question or a dropped connection costs nothing:

1. **The place:** OSM's Nominatim (`integrations/nominatim.py`), with its outline. It is asked once per name (an identifying User-Agent, as its policy asks) and kept in `vault/maps/places`.
2. **Around it:** buildings and named streets from Overpass, out to the outline plus 60 m, kept in `vault/maps/osm` (streets as `streets-<key>.json`). Streets are named ways for traffic or people on foot, not paths, steps or corridors.
3. **Its walls:** the outline's edges, with nearly straight runs joined (turns under 10°, no corner more than 1 m off); 8 m or longer. Each is walked with the building on the right, as the island sees it.
4. **Kept:** a wall whose sidewalk is open (no building where the camera stood) and that faces a named street, parallel and within 40 m; with `--street`, that street.
5. **One left:** it is used. **Several:** they are listed with their street, length, side of the building and walking direction, and the run stops; `--pick N` chooses. They are numbered before anything is measured, so a number stays put.

After the models: the facade seen must fit on the wall (2 m of slack at each end, for the outline and one view's depth), and every metre where the camera could stand is tried against the other buildings. Without `--along`, the middle of that stretch is used and `error_m` widens to cover the rest.

Geometry never tells a symmetric building's sides apart, so nothing is guessed. For Union Station
(Nov 5; Overpass did not answer the cloud workspace, so the street lines came from Nominatim):
14 walls face a street, 7 of them Front St; the three long ones are its west wing, centre
and east wing, which look alike in one view. Pick 1 is the west wing's wall, the same wall placed
by hand before; with no `--along` the camera may be anywhere from 9 m before its corner to 38 m
along it, so `error_m` is 26.5.

### The first test: Nov 5, Front St at Union Station, dusk

The shot is 114.0–122.5 s of an edited vlog (1080 × 1440, 30 fps, no GPS or date left by the edit):
an 8.5 s walk east on the Front St sidewalk with Union Station's lit front on the right.

| Step | Result |
| --- | --- |
| COLMAP 4.2 (SIFT, masks, sequential), 67 frames at 8 fps | All 67 registered, 1,849 points, 1.20 px; but the camera slid sideways and dropped 5.5 m over a 12 m walk: forward motion with mostly far points, the rotation/translation ambiguity |
| Depth chained frame to frame (SIFT + PnP on depth-lifted points, 33 of 34 frames) | Direction right (forward·walk 0.98–1.00, level), 1.0 px; but 4.85 model-metres in 8.35 s, while the frame differences show a step cadence of about 2 a second (an ordinary walk, about 10 m): each frame's depth is predicted alone and barely changes as the camera moves 30 cm |
| Rulers on the keyframe (115.2 s) | People ×0.524, ground ×0.504: ×0.514 used. Facade 2.6 m from the camera, seen from 7 to 21 m ahead |
| Islands | Seen: 242,441 splats. Imagined: 54,735 (LaMa; with Telea, 263,595 and 35,969: the wider hole moves feet and shadows from seen to imagined) |
| Fill depth, on holes cut where depth is known (the frame's real holes mirrored, and moved 250 px) | Median error 2.0% and 0.7% (90th percentile 17.6% and 5.8%), against 3.2% and 1.8% for Navier–Stokes from the edge. OpenCV 5's Telea, used before, returns about half the value on float images: its fill sat in front of the street |
| Placed | The station's west wing wall (OSM way 14744491, its north-west corner at York St), bearing 73.6°; the camera 6.5 m from the corner, at 43.645270, −79.381863, walking at 76.0°; ground 43.73 m |

Expected error: about 15 m along the wall (where on it the walk started) and 3 m across; the scale
±10% (the two rulers differ by 4%).

A walk through many frames needs poses and depth solved together. Depth Anything 3 does that
(`depth-anything-3` on PyPI; DA3-SMALL and DA3-BASE are Apache-2.0, the larger ones CC BY-NC), as
do MapAnything (its Apache-2.0 variant) and VGGT, which are not on PyPI. Installing them in the
cloud workspace was refused, so none has been run on this shot yet. That is the next step.

## Viewer

`?view=world` lists the worlds (Worlds on the calendar); `?view=world&date=<day>&world=<id>` opens
one (`components/world/WorldPage.tsx`, `CityWorld.tsx`, `lib/world.ts`). One three.js scene in the
world's frame (x east, y up, z south, metres, the origin on the ground), with four layers, each a
switch in the bar at the top, kept per viewer in local storage:

| Layer | Drawn from | Credit on screen |
| --- | --- | --- |
| Seen | Each island's splat (Spark), placed by its `enu` | |
| Known | `/api/maps/buildings` around the origin (600 m), extruded from `min_height_m` to `height_m` (at least 2.5 m), merged into one mesh, half see-through and hidden inside the seen islands' boxes (the same crop as Google's) | © OpenStreetMap contributors, ODbL 1.0 |
| Mapped | Google's Photorealistic 3D Tiles, with `3d-tiles-renderer` 0.5.4 (Apache-2.0): its Google auth plugin (the key on requests to Google's host, the session from the root), its glTF plugin with three's bundled Draco decoder (glTF build; a build plugin in `vite.config.ts` keeps the other build, about 1 MB, out of the app), and its reorientation plugin | The Google Maps logo (Google's own file from its attribution assets, unmodified: the white logo with the dark outline, for imagery; 18 px tall with its clear space; labelled Google Maps), the tiles' own copyrights, "today's map" |
| Imagined | Islands of kind `imagined`, the same way; the switch shows only when a world has one | |

**The same frame, with nothing read from the tiles.** The reorientation plugin puts the origin
(latitude and longitude in radians, `ground_m` as the height) at (0, 0, 0) with x west and z north;
the tiles' group sits under a parent turned half round, so x is east and z south. The buildings go
through `toLocal` (ECEF, then the origin's east-north-up). `scripts/world.test.mjs` runs both on
the same points: they agree within a centimetre, and 100 m north is z = −100.

**Google's terms in the code.** The key comes from `/api/maps/config` (the vault token); without
either, Mapped is off, with why. Tiles are fetched only for the view (the renderer's own
screen-space error), held in its memory cache and the browser's HTTP cache, never in Cache
Storage or IndexedDB (the e2e test checks both are empty). Nothing is measured or raycast on
them. Inside each seen island's box the tiles' fragments are discarded (a shader patch on their
materials, display only), so your footage is what shows there.

**Drawing.** A world opens where its footage was filmed (the first island's `view`), else above
its islands; orbit controls from there (double-click returns, Escape leaves). After a change,
frames are drawn for 1.2 s and at least 4 frames (on a slow GPU 1.2 s can be less than one), as
Spark sorts its splats off the main thread and the tiles stream in; then none until the next
change. A tile arriving asks for the next frames.

## Tests

`apps/viewer/e2e/world.spec.ts` (a fictional world at made-up coordinates, the Overpass answer
kept in the test vault, and a one-tile fixture tileset served in place of Google's):
- the list opens the world; the island draws in colour; the two kept buildings are drawn with
  their credit; Known off changes the picture and removes the credit; Escape goes back;
- without the token Mapped is off, with why;
- with a key, the root and then the tile are asked for with the key and the root's session; the
  tile's copyright, Google and "today's map" show; Cache Storage and IndexedDB stay empty;
  Mapped off removes the credit.

`scripts/world.test.mjs`: the frame (above), footprints (closed rings, base and top, a ring of two
points dropped), the crop boxes and the first view, and the saved layers surviving storage that
is blocked or holds junk.

`services/ingest/tests/test_world.py`, with no network:
- **The store:** a world saved whole and listed newest first; 11 kinds of broken shape refused, nothing written; splats that are not the islands' whole records refused.
- **The routes:** a world and only its islands' splats; traversal, unknown ids and other files are 404; `/health` has `world`.
- **Buildings:** footprints and their heights (`height`, storeys, feet, default; relations' closed outers only); fetched once, then served from the vault offline; 503 for an area never kept; bad parameters are 422.
- **The key:** `/maps/config` needs the token and is `null` without a key.
- **The ground:** inside Canada the bare-ground CDEM, elsewhere GLO-90; an island's `view` is two points or refused.

`services/ingest/tests/test_island.py`, the islands' geometry without the models: the ground ruler
recovers a scale of 1/2.6 and up from a tilted camera; the facade is found 6 m off on the right;
people taken as 1.70 m; the crop keeps the street and drops the sky, the far side and beyond 35 m;
an island laid on its wall lands 12 m along and 3 m out, facing away from it, walking along it.
With OpenCV (services/recon's environment; skipped elsewhere): the hole takes the feet, and the
depth behind it meets the seen depth within 1% when the second reading is 20–30% long.

`services/ingest/tests/test_walls.py`, on made-up blocks: the wall on the street is found, walked
with the building on the right, and the facade seen fits from 7 m before its corner to 42 m along;
a kiosk on the sidewalk leaves only where the walk could pass; a facade longer than the wall does
not fit; a nearly straight side is one wall and a 3 m jog is not; either winding gives the same
walls; a building with two wings on one street is listed, never guessed, and `--pick` chooses.
`test_world.py` adds: streets asked for by name, kept in their own file, asked once; a place
looked up once whatever its case and spacing, and an unknown one is an error.
- **Geometry:** the local frame (100 m north is z = −100; round trips); the geoid read between its points, wrapping at 180°; the ground height as the DEM plus the geoid.
