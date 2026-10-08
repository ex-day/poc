-- reach/sql/02_build_graph.sql
-- reach.n02_* から、路線・点・辺・駅を作る（Issue #5）。何度流しても同じ結果になる。
-- reach.line の速度を手で直した場合は、最後の「辺の所要時間」の節だけ流し直せばよい。

-- 乗換1回にかける時間（分）。歩く時間と、次の電車を待つ時間をまとめた仮の値。
-- 値を変えるときは、ここと「乗換の辺」の節を流し直す。
CREATE OR REPLACE FUNCTION reach.transfer_min() RETURNS double precision
  LANGUAGE sql IMMUTABLE AS $$ SELECT 10.0::double precision $$;

TRUNCATE reach.station, reach.edge, reach.node, reach.line RESTART IDENTITY CASCADE;

-- ---------------------------------------------------------------------------
-- 路線と種別。地下鉄は N02 の区分にないので、事業者と鉄道区分から振り分ける。
-- 表定速度は、誤差を「行けない」側に寄せるため、遅めに置く（仮の値）。
-- ---------------------------------------------------------------------------
INSERT INTO reach.line (operator, line_name, rail_class, operator_type, service_type, speed_kmh)
SELECT operator, line_name, rail_class, operator_type, service_type,
       CASE service_type
         WHEN 'shinkansen'   THEN 150
         WHEN 'conventional' THEN 40
         WHEN 'subway'       THEN 30
         WHEN 'monorail'     THEN 27
         WHEN 'agt'          THEN 25
         WHEN 'tram'         THEN 13
         WHEN 'cable'        THEN 5
         ELSE 20
       END
FROM (
  SELECT DISTINCT ON (operator, line_name)
         operator, line_name, rail_class, operator_type,
         CASE
           WHEN operator_type = '1' THEN 'shinkansen'
           WHEN operator IN ('東京地下鉄', '大阪市高速電気軌道', '北大阪急行電鉄')
                AND rail_class IN ('12', '21') THEN 'subway'
           WHEN operator_type = '3' AND rail_class = '12' THEN 'subway'
           WHEN operator = '札幌市' AND rail_class = '16' THEN 'subway'
           WHEN rail_class = '21' THEN 'tram'
           WHEN rail_class = '13' THEN 'cable'
           WHEN rail_class IN ('14', '15', '22', '23') THEN 'monorail'
           WHEN rail_class IN ('16', '24', '25') THEN 'agt'
           WHEN rail_class IN ('11', '12') THEN 'conventional'
           ELSE 'other'
         END AS service_type
  FROM reach.n02_section
  ORDER BY operator, line_name, rail_class
) s;

-- ---------------------------------------------------------------------------
-- 点：路線×区間の端点。座標は細かい揺れを丸めてから同じ点とみなす。
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE section_ends AS
SELECT s.id AS section_id, l.line_id,
       ST_SnapToGrid(ST_StartPoint(s.geom), 0.0000001) AS p_start,
       ST_SnapToGrid(ST_EndPoint(s.geom),   0.0000001) AS p_end,
       s.geom
FROM reach.n02_section s
JOIN reach.line l USING (operator, line_name);

INSERT INTO reach.node (line_id, geom)
SELECT DISTINCT line_id, p FROM (
  SELECT line_id, p_start AS p FROM section_ends
  UNION ALL
  SELECT line_id, p_end FROM section_ends
) x;

-- ---------------------------------------------------------------------------
-- 乗車の辺：区間1つが辺1つ。向きは区別しない（cost = reverse_cost）。
-- ---------------------------------------------------------------------------
INSERT INTO reach.edge (kind, line_id, section_id, source, target, length_m, geom, cost, reverse_cost)
SELECT 'ride', e.line_id, e.section_id, ns.node_id, nt.node_id,
       ST_Length(ST_Transform(e.geom, 4326)::geography), e.geom, 0, 0
FROM section_ends e
JOIN reach.node ns ON ns.line_id = e.line_id AND ns.geom = e.p_start
JOIN reach.node nt ON nt.line_id = e.line_id AND nt.geom = e.p_end;

-- ---------------------------------------------------------------------------
-- 駅：駅の線は、同じ路線の区間の1つと同じ形をしている（ホームの範囲）。その辺の両端を駅の点とする。
-- ---------------------------------------------------------------------------
INSERT INTO reach.station (station_code, station_name, group_code, line_id, edge_id, node_a, node_b, geom)
SELECT DISTINCT ON (st.station_code)
       st.station_code, st.station_name, st.group_code, l.line_id, e.id, e.source, e.target,
       ST_Transform(ST_LineInterpolatePoint(st.geom, 0.5), 4326)
FROM reach.n02_station st
JOIN reach.line l USING (operator, line_name)
LEFT JOIN reach.n02_section s
       ON s.operator = st.operator AND s.line_name = st.line_name AND ST_Equals(s.geom, st.geom)
LEFT JOIN reach.edge e ON e.section_id = s.id
ORDER BY st.station_code, e.id;

-- ---------------------------------------------------------------------------
-- 乗換の辺：同じグループの、路線の違う駅どうしをつなぐ。駅の点（node_a）どうしを結ぶ。
-- ---------------------------------------------------------------------------
INSERT INTO reach.edge (kind, source, target, cost, reverse_cost)
SELECT 'transfer', a.node_a, b.node_a, reach.transfer_min(), reach.transfer_min()
FROM reach.station a
JOIN reach.station b
  ON a.group_code = b.group_code AND a.line_id <> b.line_id AND a.station_code < b.station_code
WHERE a.node_a IS NOT NULL AND b.node_a IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 辺の所要時間（分）＝ 距離 ÷ 速度。区間ごとの上書き（reach.speed_override）があれば、そちらを使う。
-- reach.line・reach.speed_override を直したら、ここから下を流し直す。
-- ---------------------------------------------------------------------------
UPDATE reach.edge e
SET speed_kmh = l.speed_kmh
FROM reach.line l
WHERE e.kind = 'ride' AND l.line_id = e.line_id;

-- 上書き：同じ路線の2駅の間の最短の経路（その路線の辺だけで探す）に、指定の速度を当てる。
DO $$
DECLARE
  o record;
  n_edges int;
BEGIN
  FOR o IN
    SELECT so.*, l.line_id, fa.node_a AS from_node, ta.node_a AS to_node
    FROM reach.speed_override so
    JOIN reach.line l ON l.operator = so.operator AND l.line_name = so.line_name
    LEFT JOIN reach.station fa ON fa.line_id = l.line_id AND fa.station_name = so.from_station
    LEFT JOIN reach.station ta ON ta.line_id = l.line_id AND ta.station_name = so.to_station
    ORDER BY so.id
  LOOP
    IF o.from_node IS NULL OR o.to_node IS NULL THEN
      RAISE WARNING '速度の上書き % を当てられない：駅が見つからない（%、%〜%）',
        o.id, o.line_name, o.from_station, o.to_station;
      CONTINUE;
    END IF;
    UPDATE reach.edge e
    SET speed_kmh = o.speed_kmh
    FROM pgr_dijkstra(
      format('SELECT id, source, target, length_m AS cost, length_m AS reverse_cost
              FROM reach.edge WHERE kind = ''ride'' AND line_id = %s', o.line_id),
      o.from_node, o.to_node, directed := false) p
    WHERE e.id = p.edge;
    GET DIAGNOSTICS n_edges = ROW_COUNT;
    RAISE NOTICE '速度の上書き %：%（%〜%）の辺 % 本を % km/h にした',
      o.id, o.line_name, o.from_station, o.to_station, n_edges, o.speed_kmh;
  END LOOP;
END $$;

UPDATE reach.edge
SET cost = length_m / 1000.0 / speed_kmh * 60.0,
    reverse_cost = length_m / 1000.0 / speed_kmh * 60.0
WHERE kind = 'ride';

UPDATE reach.edge
SET cost = reach.transfer_min(), reverse_cost = reach.transfer_min()
WHERE kind = 'transfer';

ANALYZE reach.node;
ANALYZE reach.edge;
ANALYZE reach.station;
