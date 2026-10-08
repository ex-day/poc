-- reach/sql/07_build_walk.sql
-- reach.n13_road から、歩きのグラフを作り、駅グループ・場所を最寄りの点に結び付ける（Issue #23）。何度流しても同じ結果になる。

TRUNCATE reach.spot_walk, reach.station_walk, reach.walk_edge, reach.walk_node RESTART IDENTITY;

-- 有料の道路（高速道路など）は歩けないので除く（N13_007 = 2 を有料とみなした。要確認）
CREATE TEMP TABLE road_ends AS
SELECT id AS road_id,
       ST_SnapToGrid(ST_StartPoint(geom), 0.0000001) AS p_start,
       ST_SnapToGrid(ST_EndPoint(geom),   0.0000001) AS p_end,
       geom
FROM reach.n13_road
WHERE toll IS DISTINCT FROM '2';

INSERT INTO reach.walk_node (geom)
SELECT DISTINCT p FROM (
  SELECT p_start AS p FROM road_ends
  UNION ALL
  SELECT p_end FROM road_ends
) x;

CREATE INDEX IF NOT EXISTS walk_node_geom_idx ON reach.walk_node USING gist (geom);
ANALYZE reach.walk_node;

INSERT INTO reach.walk_edge (road_id, source, target, length_m, cost, reverse_cost, geom)
SELECT r.road_id, ns.node_id, nt.node_id, l.m, l.m / 80.0, l.m / 80.0, r.geom
FROM road_ends r
JOIN reach.walk_node ns ON ns.geom = r.p_start
JOIN reach.walk_node nt ON nt.geom = r.p_end
CROSS JOIN LATERAL (SELECT ST_Length(ST_Transform(r.geom, 4326)::geography) AS m) l;

CREATE INDEX IF NOT EXISTS walk_edge_geom_idx ON reach.walk_edge USING gist (geom);
ANALYZE reach.walk_edge;

-- 駅グループの代表点（ホームの中ほどの重心）から、いちばん近い道路の点。300m より遠ければ結び付けない（範囲の外の駅）
INSERT INTO reach.station_walk (group_code, node_id, access_m)
SELECT g.group_code, n.node_id, n.d
FROM (
  SELECT group_code, ST_Transform(ST_Centroid(ST_Collect(geom)), 6668) AS p
  FROM reach.station GROUP BY group_code
) g
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, ST_Transform(g.p, 4326)::geography) AS d
  FROM reach.walk_node w
  ORDER BY w.geom <-> g.p
  LIMIT 1
) n
WHERE n.d <= 300;

-- 場所も同じ
INSERT INTO reach.spot_walk (spot_id, node_id, access_m)
SELECT s.spot_id, n.node_id, n.d
FROM reach.spot s
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, s.geom::geography) AS d
  FROM reach.walk_node w
  ORDER BY w.geom <-> ST_Transform(s.geom, 6668)
  LIMIT 1
) n
WHERE n.d <= 300;

ANALYZE reach.station_walk;
ANALYZE reach.spot_walk;
