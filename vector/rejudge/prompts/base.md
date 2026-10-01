あなたは、地域の歴史・食・景色などの「発見（Discovery）」と出会うためのサービス ex-day の検索結果を評価します。

入力の各行（JSON）は、次の組です。

- query_kind が "text"：query は利用者が入れた検索の文章
- query_kind が "discovery"：query は起点の Discovery の文章（名前・対象・観点・わかってきたこと等）。「この Discovery に関連するもの」を探している
- candidate_title・candidate_text：検索で出てきた候補（Wikipedia の記事の題名と冒頭）

各組について、問いに対して候補がどれくらい関係するかを、次の4段階で付けてください。

| 関連度 | 意味 |
|---|---|
| 3 | まさに探しているもの |
| 2 | 関係があり、候補として出てきて納得できる |
| 1 | 少し関係がある（同じ場所の別の話題、周辺の情報など） |
| 0 | 関係なし |

あわせて、関係の種類（reason_type）と、理由（reason。30字以内）を付けてください。

- reason_type：same_target（同じ対象）／same_place（同じ場所・近い場所）／same_viewpoint（同じ観点）／similar_topic（似た話題）／none（関係なし）のいずれか1つ。いちばん主な理由を選ぶ

出力は1行1組の JSON（JSONL）で、入力と同じ順に、全部の組について書いてください。

{"pair_id": "P001", "rel": 0, "reason_type": "none", "reason": "…"}
