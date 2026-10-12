-- reach/sql/09_access_point.sql
-- 範囲のある場所の到達点（入口・範囲の端）を入れる表と、到達点で歩きを出す関数（Issue #31）。
-- 入れ方は reach/entrance/README.md。流すと中身は消える。

DROP TABLE IF EXISTS reach.station_spot_walk_ap, reach.spot_walk_ap, reach.spot_access_point CASCADE;

-- 場所の到達点。1つの場所に複数（入口が3つある公園なら3点）。
--   osm_entrance：OSM の範囲の境界に乗っている entrance=*・門
--   osm_crossing：歩ける道（OSM の highway）が範囲の境界と交わる点（OSM に入口の印が無い場所）
--   area_edge   ：範囲の境界を一定の間隔で区切った点（入口の印も道との交わりも無い場所。湖など）
CREATE TABLE reach.spot_access_point (
  ap_id   serial PRIMARY KEY,
  spot_id int NOT NULL,
  source  text NOT NULL CHECK (source IN ('osm_entrance', 'osm_crossing', 'area_edge')),
  kind    text,
  lon     double precision NOT NULL,
  lat     double precision NOT NULL,
  geom    geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);

-- 場所と道路の点の対応の、到達点の版。到達点の無い場所は、今の点（reach.spot_walk）をそのまま入れる
CREATE TABLE reach.spot_walk_ap (
  spot_id  int NOT NULL,
  ap_id    int,                -- NULL は今の点
  node_id  bigint NOT NULL REFERENCES reach.walk_node,
  access_m double precision NOT NULL
);
CREATE INDEX spot_walk_ap_node_idx ON reach.spot_walk_ap (node_id);

-- reach.station_spot_walk の、到達点の版（場所ごとに、いちばん近い到達点までの歩き）
CREATE TABLE reach.station_spot_walk_ap (
  group_code text NOT NULL,
  spot_id    int NOT NULL,
  walk_min   double precision NOT NULL,
  PRIMARY KEY (group_code, spot_id)
);

-- reach.walk_from_station と同じ。場所の道路の点を reach.spot_walk_ap から取る（1つの場所に複数の点があれば、いちばん近いもの）
CREATE OR REPLACE FUNCTION reach.walk_from_station_ap(p_group_code text, p_max_min double precision)
RETURNS TABLE (spot_id int, walk_min double precision)
LANGUAGE sql STABLE AS $$
  WITH st AS (
    SELECT sw.node_id, sw.access_m, w.geom
    FROM reach.station_walk sw JOIN reach.walk_node w USING (node_id)
    WHERE sw.group_code = p_group_code
  ),
  dd AS (
    SELECT d.node, d.agg_cost
    FROM st,
    LATERAL pgr_drivingDistance(
      format(
        'SELECT id, source, target, cost, reverse_cost FROM reach.walk_edge WHERE geom && ST_Expand(%L::geometry, %s)',
        st.geom, (p_max_min * 80.0) / 80000.0),
      st.node_id, GREATEST(p_max_min - st.access_m / 80.0, 0), directed := false) d
  )
  SELECT sw.spot_id,
         min(dd.agg_cost + (SELECT access_m FROM st) / 80.0 + sw.access_m / 80.0)
  FROM dd
  JOIN reach.spot_walk_ap sw ON sw.node_id = dd.node
  GROUP BY sw.spot_id
  HAVING min(dd.agg_cost + (SELECT access_m FROM st) / 80.0 + sw.access_m / 80.0) <= p_max_min;
$$;
