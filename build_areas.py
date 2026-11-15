#!/usr/bin/env python3
"""
The areas file: every named sub-city area in the world, as one FlatGeobuf with a spatial index,
for log-walk to find the neighbourhood a walk is inside (docs/specs/neighbourhood-areas.md).

    python -I scripts/areas/build_areas.py --out areas.fgb                      # worldwide
    python -I scripts/areas/build_areas.py --bbox -106.85,52.05,-106.5,52.25 --out sk.fgb
    python -I scripts/areas/build_areas.py --print-release

Needs `duckdb` (scripts/areas/requirements.txt). Reads Overture Maps `divisions/division_area`
(OpenStreetMap-derived, ODbL) straight from the public GeoParquet on S3 with DuckDB; nothing is
downloaded whole, and no OSM API is ever called.

What goes in:
  - the neighbourhood levels, `microhood`, `neighborhood` and `macrohood`;
  - Tokyo's 23 special wards, which Overture holds as `locality` areas in JP-13 named with 区.
    A ward is a level above the neighbourhood there (log-walk/areas.ts), so log-walk needs it too.
  - only areas whose geometry comes from OpenStreetMap (Overture also carries geoBoundaries,
    LINZ and Brazil's DadosAbertos, under other licences), on land, with a usable name (names.py).

Each area's size (`km2`) is measured on the full outline; the outline is then simplified the way
the app's own shapes are (supabase/functions/neighbourhood-shape/shape.ts `simplify`): islands
and holes smaller than 2% of the area's span dropped, then simplified with topology kept until
it has at most MAX_POINTS points. Holes that remain are kept.
"""
import argparse
import json
import os
import re
import struct
import sys
import tempfile
import time

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # names.py; `python -I` leaves it off
from names import has_letter, pick_en, pick_lat, pick_local  # noqa: E402

# The Overture release the file is built from. Overture publishes monthly; the quarterly workflow
# (.github/workflows/areas-build.yml) builds this one unless told otherwise. Bump it here only.
RELEASE = '2026-10-21.0'
SOURCE = 's3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*'

LEVELS = ('microhood', 'neighborhood', 'macrohood')
TOKYO_WARDS = 23
INDEX_NODE_SIZE = 16  # GDAL's FlatGeobuf default, checked in every file written
# The app's shape budget (shape.ts MAX_POINTS) and the island cut-off simplify() uses.
MAX_POINTS = 200
PART_SPAN = 0.02
# Douglas-Peucker tolerance ladder, as simplify(): span/2000, then 1.6 times coarser each round.
TOL_START = 1 / 2000
TOL_STEP = 1.6


def parse_bbox(text):
    parts = [float(x) for x in text.split(',')]
    if len(parts) != 4 or parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise argparse.ArgumentTypeError('bbox is minx,miny,maxx,maxy')
    return parts


def connect(work, memory_limit):
    con = duckdb.connect(os.path.join(work, 'areas.duckdb'))
    for ext in ('httpfs', 'spatial'):
        con.execute(f'INSTALL {ext}; LOAD {ext};')
    # Overture's bucket is public: read it anonymously, whatever AWS keys the environment holds
    # (the workflow's are for R2, and DuckDB would send them to AWS and be refused).
    con.execute("""CREATE OR REPLACE SECRET overture (TYPE s3, PROVIDER config, KEY_ID '', SECRET '',
                     REGION 'us-west-2', SCOPE 's3://overturemaps-us-west-2')""")
    con.execute('SET preserve_insertion_order=false;')
    con.execute('SET enable_progress_bar=false;')
    con.execute(f"SET temp_directory='{os.path.join(work, 'tmp')}';")
    if memory_limit:
        con.execute(f"SET memory_limit='{memory_limit}';")
    return con


def log(msg, t0):
    print(f'  {time.time() - t0:7.1f}s  {msg}', file=sys.stderr, flush=True)


def read_areas(con, source, bbox):
    where = ''
    if bbox:
        x0, y0, x1, y1 = bbox
        where = f'and bbox.xmin <= {x1} and bbox.xmax >= {x0} and bbox.ymin <= {y1} and bbox.ymax >= {y0}'
    levels = ', '.join(f"'{s}'" for s in LEVELS)
    con.execute(f"""
        create or replace table src as
        select id, division_id as div, subtype as sub, country as cc,
               names."primary" as primary_name,
               map_keys(names.common) as ck, map_values(names.common) as cv,
               list_transform(names.rules, r -> r.language) as rl,
               list_transform(names.rules, r -> r.variant) as rv,
               list_transform(names.rules, r -> r."value") as rval,
               geometry as geom
        from read_parquet('{source}', hive_partitioning=1)
        where (subtype in ({levels})
               or (subtype = 'locality' and country = 'JP' and region = 'JP-13' and names."primary" like '%区'))
          and sources[1].dataset = 'OpenStreetMap'
          and coalesce(is_land, true) and coalesce(class, 'land') = 'land'
          {where}
    """)


def name_areas(con, work):
    """(en, lat, local) for every area, from names.py; areas with none of them are dropped."""
    path = os.path.join(work, 'names.jsonl')
    dropped = 0
    with open(path, 'w', encoding='utf-8') as f:
        cur = con.execute('select id, primary_name, ck, cv, rl, rv, rval from src')
        while True:
            rows = cur.fetchmany(50_000)
            if not rows:
                break
            for id_, primary, ck, cv, rl, rv, rval in rows:
                common = dict(zip(ck or [], cv or []))
                rules = [{'language': a, 'variant': b, 'value': c} for a, b, c in zip(rl or [], rv or [], rval or [])]
                en = pick_en(primary, common, rules)
                en = en if has_letter(en) else None
                lat = pick_lat(primary)
                local = pick_local(primary)
                if en is None and lat is None and local is None:
                    dropped += 1
                    continue
                f.write(json.dumps({'id': id_, 'en': en, 'lat': lat, 'local': local}, ensure_ascii=False) + '\n')
    con.execute(f"""
        create or replace table named as
        select s.id, s.div, s.sub, s.cc, n.en, n.lat, n."local",
               ST_Area_Spheroid(ST_FlipCoordinates(s.geom)) / 1e6 as km2,
               case when ST_IsValid(s.geom) then s.geom else ST_CollectionExtract(ST_MakeValid(s.geom), 3) end as geom
        from src s
        join read_json('{path}', columns = {{'id': 'VARCHAR', 'en': 'VARCHAR', 'lat': 'VARCHAR', 'local': 'VARCHAR'}}, format = 'newline_delimited') n using (id)
    """)
    con.execute('drop table src')
    return dropped


def simplify_areas(con, t0):
    """Each outline to at most MAX_POINTS points, as shape.ts simplify() does."""
    # 1. Islands and holes under PART_SPAN of the area's span go: they cost points and draw as dust.
    con.execute(f"""
        create or replace table cleaned as
        with f as (
            select id, geom, greatest(ST_XMax(geom) - ST_XMin(geom), ST_YMax(geom) - ST_YMin(geom)) as span from named
        ), d as (
            select id, span, unnest(ST_Dump(geom)).geom as p from f
        ), k as (
            select id, span,
                   ST_MakePolygon(ST_ExteriorRing(p), list_filter(
                       list_transform(range(1, ST_NInteriorRings(p) + 1), i -> ST_InteriorRingN(p, i::INTEGER)),
                       r -> greatest(ST_XMax(r) - ST_XMin(r), ST_YMax(r) - ST_YMin(r)) >= span * {PART_SPAN})) as p
            from d
            where ST_GeometryType(p) = 'POLYGON'
              and greatest(ST_XMax(p) - ST_XMin(p), ST_YMax(p) - ST_YMin(p)) >= span * {PART_SPAN}
        )
        select id, any_value(span) as span, ST_Multi(ST_Collect(list(p))) as g0 from k group by id
    """)
    # 2. Douglas-Peucker with topology kept (no ring crosses itself or another), coarser each
    #    round for the areas still over budget.
    con.execute(f"""
        create or replace table simple as
        select id, span, g0, span * {TOL_START} as tol, ST_SimplifyPreserveTopology(g0, span * {TOL_START}) as g from cleaned
    """)
    con.execute('drop table cleaned')
    rounds = 0
    while True:
        n = con.execute(f'select count(*) from simple where ST_NPoints(g) > {MAX_POINTS} and tol * {TOL_STEP} < span').fetchone()[0]
        if n == 0:
            break
        rounds += 1
        con.execute(f"""
            update simple set tol = tol * {TOL_STEP}, g = ST_SimplifyPreserveTopology(g0, tol * {TOL_STEP})
            where ST_NPoints(g) > {MAX_POINTS} and tol * {TOL_STEP} < span
        """)
        log(f'simplify round {rounds}: {n} areas over {MAX_POINTS} points', t0)
    # 3. An archipelago can still be over budget (every ring keeps at least 4 points): keep its
    #    largest parts that fit, and the largest one's outer ring alone if even that does not.
    over = con.execute(f'select count(*) from simple where ST_NPoints(g) > {MAX_POINTS}').fetchone()[0]
    if over:
        con.execute(f"""
            create or replace table trimmed as
            with d as (
                select id, tol, unnest(ST_Dump(g)).geom as p from simple where ST_NPoints(g) > {MAX_POINTS}
            ), r as (
                select id, tol, p,
                       sum(ST_NPoints(p)) over (partition by id order by ST_Area(p) desc rows unbounded preceding) as run,
                       row_number() over (partition by id order by ST_Area(p) desc) as rn
                from d
            )
            select id, ST_Multi(ST_Collect(list(
                       case when rn = 1 and ST_NPoints(p) > {MAX_POINTS}
                            then ST_SimplifyPreserveTopology(ST_MakePolygon(ST_ExteriorRing(p)), tol) else p end))) as g
            from r where run <= {MAX_POINTS} or rn = 1 group by id
        """)
        con.execute('update simple set g = t.g from trimmed t where simple.id = t.id')
        con.execute('drop table trimmed')
    # 4. Any outline simplification left invalid is repaired.
    invalid = con.execute('select count(*) from simple where not ST_IsValid(g)').fetchone()[0]
    if invalid:
        con.execute('update simple set g = ST_Multi(ST_CollectionExtract(ST_MakeValid(g), 3)) where not ST_IsValid(g)')
    return {'rounds': rounds, 'trimmed': over, 'repaired': invalid}


def fgb_header(path):
    """Feature count and index node size from a FlatGeobuf header (0 means no spatial index)."""
    with open(path, 'rb') as f:
        head = f.read(12)
        if head[:3] != b'fgb' or head[4:7] != b'fgb':
            raise SystemExit(f'{path} is not a FlatGeobuf')
        size = struct.unpack('<I', head[8:12])[0]
        buf = f.read(size)
    table = struct.unpack_from('<I', buf, 0)[0]
    vtable = table - struct.unpack_from('<i', buf, table)[0]
    vsize = struct.unpack_from('<H', buf, vtable)[0]

    def field(i, fmt, default):
        at = 4 + 2 * i
        if at >= vsize:
            return default
        off = struct.unpack_from('<H', buf, vtable + at)[0]
        return struct.unpack_from(fmt, buf, table + off)[0] if off else default

    # Header fields 8 and 9 (flatgeobuf header.fbs): features_count, index_node_size (default 16).
    return {'features': field(8, '<Q', 0), 'index_node_size': field(9, '<H', 16)}


def write_fgb(con, out, release):
    # Written beside the target and moved into place, so a failed build never leaves a partial
    # file under the real name. The driver writes a directory unless the name ends in .fgb.
    tmp = out[:-4] + '.part.fgb'
    for p in (out, tmp):
        if os.path.exists(p):
            os.remove(p)
    con.execute(f"""
        COPY (
            select n.id, n.div, n.sub, n.cc, n.en, n.lat, n."local", n.km2::DOUBLE as km2, '{release}' as rel, ST_Multi(s.g) as geom
            from named n join simple s using (id)
            where not ST_IsEmpty(s.g)
        ) TO '{tmp}' (FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326', GEOMETRY_TYPE 'MULTIPOLYGON',
                      LAYER_NAME 'areas', LAYER_CREATION_OPTIONS 'SPATIAL_INDEX=YES')
    """)
    os.replace(tmp, out)


def summarise(con, release, bbox, out, seconds, dropped, simp):
    by_sub = dict(con.execute("""
        select n.sub, count(*) from named n join simple s using (id) where not ST_IsEmpty(s.g) group by 1 order by 1
    """).fetchall())
    pts = con.execute('select max(ST_NPoints(g)), avg(ST_NPoints(g)), sum(ST_NPoints(g)) from simple').fetchone()
    names = con.execute("""
        select count(*) filter (where en is not null), count(*) filter (where lat is not null),
               count(*) filter (where "local" is not null), count(*) filter (where en is null)
        from named
    """).fetchone()
    return {
        'release': release,
        'bbox': bbox,
        'out': out,
        'bytes': os.path.getsize(out),
        'seconds': round(seconds, 1),
        'features': sum(by_sub.values()),
        'by_subtype': by_sub,
        'tokyo_wards': by_sub.get('locality', 0),
        'points': {'max': pts[0], 'mean': round(pts[1] or 0, 1), 'total': pts[2]},
        'names': {'en': names[0], 'lat': names[1], 'local': names[2], 'local_only': names[3], 'dropped_unnamed': dropped},
        'simplify': simp,
    }


def build(out, release=RELEASE, source=None, bbox=None, work=None, memory_limit=None):
    t0 = time.time()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.\d+', release):
        raise SystemExit(f'bad release: {release}')
    source = source or SOURCE.format(release=release)
    out = os.path.abspath(out)
    if not out.endswith('.fgb'):
        raise SystemExit('--out must end in .fgb')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        con = connect(tmp, memory_limit)
        read_areas(con, source, bbox)
        log(f"read {con.execute('select count(*) from src').fetchone()[0]} areas from {source}", t0)
        dropped = name_areas(con, tmp)
        log(f'named; {dropped} without a usable name dropped', t0)
        simp = simplify_areas(con, t0)
        log(f'simplified {simp}', t0)
        write_fgb(con, out, release)
        log(f'wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)', t0)
        summary = summarise(con, release, bbox, out, time.time() - t0, dropped, simp)
        con.close()
    # The reader walks a packed R-tree of GDAL's default node size; a node size of 1 (or 0, no
    # index) would make every lookup read far more of the file.
    head = fgb_header(out)
    summary['index_node_size'] = head['index_node_size']
    if head['index_node_size'] != INDEX_NODE_SIZE or head['features'] != summary['features']:
        raise SystemExit(f'unexpected FlatGeobuf header {head}, wanted node size {INDEX_NODE_SIZE} and {summary["features"]} features')
    if bbox is None and summary['tokyo_wards'] != TOKYO_WARDS:
        raise SystemExit(f"expected Tokyo's {TOKYO_WARDS} special wards, found {summary['tokyo_wards']}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--out', help='the .fgb to write')
    ap.add_argument('--bbox', type=parse_bbox, help='minx,miny,maxx,maxy (lon/lat); worldwide when absent')
    ap.add_argument('--release', default=RELEASE)
    ap.add_argument('--source', help='division_area parquet path or glob (default: the release on S3)')
    ap.add_argument('--work', help='directory for the scratch database (default: system temp)')
    ap.add_argument('--memory-limit', help="DuckDB memory_limit, e.g. '5GB'")
    ap.add_argument('--summary', help='also write the summary JSON here')
    ap.add_argument('--print-release', action='store_true', help='print the pinned release and exit')
    args = ap.parse_args()
    if args.print_release:
        print(RELEASE)
        return
    if not args.out:
        ap.error('--out is required')
    summary = build(args.out, args.release, args.source, args.bbox, args.work, args.memory_limit)
    text = json.dumps(summary, indent=1, ensure_ascii=False)
    if args.summary:
        with open(args.summary, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
