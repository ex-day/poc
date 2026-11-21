-- reach/osmwalk/base.sql
-- N13 と OSM で歩きを比べる（Issue #46）ための、駅と場所。#31 で DB から書き出した CSV（issue31_station.csv・issue31_spot.csv）を入れる。
-- 歩きの比べ方は #23・#31 と同じにするので、表の名前と列は 07〜10 が使うもの（reach.station・reach.spot）にそろえる。
-- 流し方は README.md。N13 用と OSM 用の2つの DB のそれぞれに流す。

CREATE SCHEMA IF NOT EXISTS reach;
DROP TABLE IF EXISTS reach.station, reach.spot CASCADE;

CREATE TABLE reach.station (
  station_code text PRIMARY KEY,
  station_name text NOT NULL,
  group_code   text NOT NULL,
  line_id      int,
  lon          double precision NOT NULL,
  lat          double precision NOT NULL,
  geom         geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX ON reach.station (group_code);

CREATE TABLE reach.spot (
  spot_id int PRIMARY KEY,
  source  text NOT NULL,
  ext_id  text NOT NULL,
  name    text NOT NULL,
  kinds   text,
  lon     double precision NOT NULL,
  lat     double precision NOT NULL,
  geom    geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX ON reach.spot USING gist ((geom::geography));
