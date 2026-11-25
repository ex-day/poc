-- reach/osmwalk/schema_osm.sql
-- OSM の歩ける道で作る歩きのグラフ（Issue #46）。表の名前と列は 06_walk.sql（N13）にそろえ、07〜10 の計算をそのまま使えるようにする。
-- N13 との違い：辺は OSM の way を交差点で区切った区間（osm_way_id・highway を持つ）、点の ID は OSM の node の ID。
-- 流す順：schema_osm.sql → load_osm_walk.py → build_osm.sql → walk_precompute.sql → ../sql/09・到達点の CSV・../sql/10

DROP TABLE IF EXISTS reach.station_spot_walk, reach.spot_walk, reach.station_walk, reach.walk_edge, reach.walk_node CASCADE;

CREATE TABLE reach.walk_node (
  node_id bigint PRIMARY KEY,                 -- OSM の node の ID
  geom    geometry(Point, 6668) NOT NULL
);

CREATE TABLE reach.walk_edge (
  id           bigserial PRIMARY KEY,
  osm_way_id   bigint NOT NULL,
  highway      text NOT NULL,
  source       bigint NOT NULL,
  target       bigint NOT NULL,
  length_m     double precision NOT NULL,
  cost         double precision NOT NULL,
  reverse_cost double precision NOT NULL,
  geom         geometry(LineString, 6668) NOT NULL
);

CREATE TABLE reach.station_walk (
  group_code text PRIMARY KEY,
  node_id    bigint NOT NULL REFERENCES reach.walk_node,
  access_m   double precision NOT NULL
);
CREATE TABLE reach.spot_walk (
  spot_id  int PRIMARY KEY,
  node_id  bigint NOT NULL REFERENCES reach.walk_node,
  access_m double precision NOT NULL
);
CREATE TABLE reach.station_spot_walk (
  group_code text NOT NULL,
  spot_id    int NOT NULL,
  walk_min   double precision NOT NULL,
  PRIMARY KEY (group_code, spot_id)
);
