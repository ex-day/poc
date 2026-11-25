-- reach/osmwalk/exit_walk.sql
-- 駅の代表点を、ホームの重心から OSM の駅の出入口（#31 の station_exit_point.csv）に替えたときの歩き（Issue #46）。
-- OSM 用の DB で、10_build_access_walk.sql のあとに流す。出入口のある駅グループだけ。
-- 出入口ごとに歩きをたどり、（駅グループ, 場所）ごとにいちばん短いものを取る。駅の中（ホームから出入口まで）の歩きは足さない。

DROP TABLE IF EXISTS reach.station_exit, reach.station_exit_walk, reach.station_spot_walk_exit CASCADE;

CREATE TABLE reach.station_exit (
  group_code text NOT NULL,
  node_id    bigint NOT NULL,           -- OSM の出入口の node の ID（歩きのグラフの点とは限らない）
  railway    text,
  name       text,
  lon        double precision NOT NULL,
  lat        double precision NOT NULL,
  dist_m     double precision,          -- ホームの重心からの直線（#31）
  geom       geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);

-- 出入口と、いちばん近い歩きのグラフの点（300m まで。07 と同じ）
CREATE TABLE reach.station_exit_walk (
  group_code text NOT NULL,
  exit_node  bigint NOT NULL,
  node_id    bigint NOT NULL,
  access_m   double precision NOT NULL
);

CREATE TABLE reach.station_spot_walk_exit (
  group_code text NOT NULL,
  spot_id    int NOT NULL,
  walk_min   double precision NOT NULL,
  PRIMARY KEY (group_code, spot_id)
);

-- 1つの点から片道 p_max_min 分までに歩いて行ける場所（到達点の版）。walk_from_station_ap の起点を点にしたもの
CREATE OR REPLACE FUNCTION reach.walk_from_node_ap(p_node bigint, p_access_m double precision, p_max_min double precision)
RETURNS TABLE (spot_id int, walk_min double precision)
LANGUAGE sql STABLE AS $$
  WITH st AS (SELECT w.geom FROM reach.walk_node w WHERE w.node_id = p_node),
  dd AS (
    SELECT d.node, d.agg_cost
    FROM st,
    LATERAL pgr_drivingDistance(
      format(
        'SELECT id, source, target, cost, reverse_cost FROM reach.walk_edge WHERE geom && ST_Expand(%L::geometry, %s)',
        st.geom, (p_max_min * 80.0) / 80000.0),
      p_node, GREATEST(p_max_min - p_access_m / 80.0, 0), directed := false) d
  )
  SELECT sw.spot_id, min(dd.agg_cost + p_access_m / 80.0 + sw.access_m / 80.0)
  FROM dd
  JOIN reach.spot_walk_ap sw ON sw.node_id = dd.node
  GROUP BY sw.spot_id
  HAVING min(dd.agg_cost + p_access_m / 80.0 + sw.access_m / 80.0) <= p_max_min;
$$;
