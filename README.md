# pli - photo location interpolater

`pli` fills the GPS hole in a photo folder. Some cameras (and most action cams,
drones and phone apps without location permission) only record a position every
now and then, or not at all. `pli` reads the shooting timeline, works out how
fast the camera must have been moving, and writes a reconstructed position into
the EXIF of every photo that is missing one.

```
pli photos/ --dry-run      # see what would be written, change nothing
pli photos/                # do it, keeping a .bak copy of every file
```

## Why not just interpolate against time?

Time-linear interpolation (what most GPS-taggers do) assumes you moved at a
constant speed between two fixes. That is rarely true:

```
   fix A ---------------------------- fix B     1 km apart, 20 min apart
   time-linear:   photos land evenly between A and B
   reality:       you walked for 4 min, then stood at a viewpoint for 16
```

`pli` builds a **speed profile** instead:

1. Each located photo gets an estimated ground speed. If the file already has
   `GPSSpeed`, that value wins; otherwise the speed is the distance to the
   neighbouring fixes divided by the time between them, smoothed with a
   Fritsch-Carlson monotone cubic so it never overshoots or oscillates.
2. The travelled fraction of a segment is the **integral of that speed curve**
   between the two anchors, evaluated at the photo's timestamp and normalised by
   the integral over the whole segment. Standing still therefore keeps a photo
   next to the anchor where you stopped, instead of dragging it toward the middle
   of the segment.
3. Distances are computed on a local tangent plane (equirectangular projection
   around the first fix), which handles the antimeridian and keeps the maths
   cheap and two-dimensional.

`--method linear` reproduces the classic time-linear behaviour, and
`--method spline` fits a monotone cubic *through the fixes themselves* (useful
when the track curves and there are at least three fixes). Every estimate also
reports an `+/-Nm` uncertainty derived from the anchor gap, plus a short note
(`speed`, `linear`, `spline`, `stationary`, `extrapolate`).

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # the only runtime dependency is piexif
pli --help
```

Without an install, `PYTHONPATH=src python -m pli --help` works from a checkout.

## Try it in ten seconds

`pli demo` writes a synthetic trip: 11 photos, 3 of them with GPS, a brisk walk
followed by a long stop - exactly the case where time-linear is wrong.

```bash
pli demo /tmp/trip
pli inspect /tmp/trip                     # timeline and where the gaps are
pli /tmp/trip --dry-run                   # speed model preview
pli /tmp/trip --dry-run --method linear   # compare with the classic model
pli /tmp/trip                             # write it for real
```

## Commands

| Command | What it does |
| --- | --- |
| `pli FOLDER` | same as `pli interpolate FOLDER` (the default) |
| `pli interpolate FOLDER` | reconstruct and write positions |
| `pli inspect FOLDER` | print the timeline, the located photos and the gaps |
| `pli demo FOLDER` | create a synthetic folder for experiments and tests |

Useful flags for `interpolate`:

| Flag | Meaning |
| --- | --- |
| `--dry-run` | report only, never write |
| `-o, --output-dir DIR` | write copies into `DIR` instead of editing in place |
| `--no-backup` | skip the `.bak` copy made when editing in place |
| `--method speed\|linear\|spline` | interpolation strategy (default `speed`) |
| `--extrapolate` | also place photos taken before the first / after the last fix |
| `--max-gap-seconds N` | refuse to bridge a longer time gap (default 21600) |
| `--max-gap-distance N` | refuse to bridge a longer distance (default 50 km) |
| `--stationary-meters N` | anchor pairs closer than this are "standing still" (12 m) |
| `--tz MODE` | time zone handling, see below |
| `--mtime-fallback` | use the file mtime when a photo has no EXIF date |
| `--no-speed`, `--no-track`, `--no-gps-time` | do not write those optional tags |
| `--report PATH`, `--json` | machine readable output |
| `--ext jpg,png`, `--no-recursive`, `--include-hidden`, `--jobs N`, `--backend` | scanning |

## Time zones

EXIF timestamps are naive wall-clock text; a timeline needs instants. `pli`
resolves them with `--tz`:

* `auto` (default) - honour any recorded `OffsetTimeOriginal`; for photos
  without one, assume the theory time zone of the nearest located photo's
  longitude (15 degree wide zones). Photos that carry their own offset always
  win, so a camera that records one is never second-guessed.
* `utc` - like `auto`, but the fallback assumption is UTC.
* `+HH:MM` - the fallback assumption is that fixed offset.
* `as-written` - pure wall clock, offsets ignored (the classic `gpscorrelate`
  behaviour; fine for one camera that never left its home zone).

Theory zones ignore political borders, so a photo taken in Seoul (126.98 E) is
assumed `+08:00` unless the file says otherwise; pass `--tz +09:00` when you
know better. Dates printed by the report are shown in your local time zone.

## What gets written

The GPS IFD is added, and when the timestamp is known well enough the
`DateTimeOriginal` / `OffsetTimeOriginal` text stamps are refreshed so that
other tools read the same instant that was interpolated. Nothing else is
touched: image data, camera settings, thumbnails and the original camera
timestamps all survive. Writes are atomic (temp file plus `os.replace`), and
in-place edits keep a `NAME.jpg.bak` copy unless you pass `--no-backup`.

GPS tags written: `GPSLatitude(+Ref)`, `GPSLongitude(+Ref)`,
`GPSAltitude(+Ref)`, `GPSSpeed(+Ref)`, `GPSTrack(+Ref)`, `GPSDateStamp`,
`GPSTimeStamp` and `GPSProcessingMethod` (set to
`photo-location-interpolater`, so you can find the guesses again).

## Supported files

| Format | Read | Write |
| --- | --- | --- |
| JPEG (`.jpg`, `.jpeg`) | yes | yes |
| PNG (`.png`, own `eXIf` chunk) | yes | yes |
| WEBP | yes | yes |
| TIFF (`.tif`, `.tiff`) | yes | no (analysed, never rewritten) |
| RAW (`.cr2`, `.nef`, `.arw`, ...) | no | no |

TIFF/RAW files are deliberately read-only: re-writing an arbitrary IFD chain
safely needs a full container writer, and corrupting a raw file is worse than
leaving it alone. They still take part in the timeline, so they can act as
anchors for the photos around them.

## Limitations

* A photo taken before the first fix or after the last one is skipped unless
  `--extrapolate` is given; extrapolated positions decay exponentially and are
  capped by `--max-extrapolation-seconds` / `--max-extrapolation-meters`.
* Speeds inferred from anchor geometry describe *average* movement between
  fixes; a detour that left the straight line cannot be recovered.
* `piexif` does not understand the `OffsetTime*` tags, so `pli` parses that
  handful of ASCII tags itself (`pli.exifio.tifftags`).
* If the camera clock itself was wrong, no EXIF text can fix it; `pli` only
  re-derives stamps from the timeline it was given.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

The test-suite covers the geodesy, the speed model and its edge cases (identical
anchors, antimeridian, a single fix, extrapolation), timestamp and time zone
parsing, EXIF round-trips through JPEG/PNG/WEBP, timeline construction, the
pipeline (dry runs, backups, output folders, write failures) and the CLI.
