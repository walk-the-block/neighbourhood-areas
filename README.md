# Neighbourhood areas

This is how [Walk the Block](https://walktheblock.co) builds the file it uses to name the neighbourhood a walk is in. The file holds every named neighbourhood-sized area in the world as one [FlatGeobuf](https://flatgeobuf.org) file with a spatial index. The app reads a few kilobytes of it per walk, using HTTP range requests.

## Where the data comes from

The areas come from the `divisions/division_area` theme of [Overture Maps](https://overturemaps.org), read straight from Overture's public GeoParquet on S3. Only areas whose geometry comes from OpenStreetMap are kept, so the whole file is OpenStreetMap data:

> © OpenStreetMap contributors, available under the [Open Database License](https://opendatacommons.org/licenses/odbl/). Distributed by Overture Maps.

This repository is the method we use to make that derived database, published as the ODbL asks.

## What goes in

- Areas at Overture's three neighbourhood levels: `microhood`, `neighborhood` and `macrohood`.
- Tokyo's 23 special wards. Overture holds them as `locality` areas, and in Tokyo the ward is the level above the neighbourhood.
- Only areas on land that have a usable name.

Each area keeps its English or Latin-script name (`en`), its own name when that is in Latin letters (`lat`), and its own name when it is in another script (`local`). `names.py` explains the order these are picked in, and nothing is transliterated.

Each area's size in km² (`km2`) is measured on the full outline. The outline is then simplified to at most 200 points: islands and holes smaller than 2% of the area's span are dropped first, then the shape is simplified with its topology kept.

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -I build_areas.py --out areas.fgb
```

For a test, add a bounding box: `--bbox -106.85,52.05,-106.5,52.25`. A worldwide build reads about 460,000 areas and takes a while on a laptop; `--memory-limit 5GB` keeps DuckDB within bounds.

The name rules have tests, which need nothing installed:

```bash
python -I -m unittest discover -p 'test_*.py'
```

Some comments mention files in our app, such as `log-walk` and `shape.ts`. Those live in our private app repository; the scripts here don't need them.

## Licence

The code is under the MIT licence (see `LICENSE`). Any data you build with it is OpenStreetMap data under the ODbL, with the attribution above.
