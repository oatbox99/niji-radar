# niji_gunma — ニジマス釣行判断システム（群馬・河川C&R/特別釣場）

個人運用＋一般公開の意思決定ツール。週末遠征の無駄足を減らす。自分の釣果を含まず公開データのみで作る設計のため**公開可能**（無料公開手順→README）。
Python 3.x + Streamlit + SQLite + Gemini(gemini-2.5-flash)。判定単位は **区間(reach)**。鮎レーダー(ayu_gunma)を土台に「品質軸」を鮎(垢/G-Index)→鱒(水温/TSI)へ作り直した姉妹プロジェクト（完全独立）。

## 恒久制約（エビデンス調査 2026-07 で確定・厳守）

- **水温がニジマス判定の第一決定因子**（気温ではない）。摂餌至適 10–16℃、20℃超で摂餌停止域、24℃接近で生息不適（群馬県水試/EPA/USGS/青森県産技セ）。
- **水温のライブ源は存在しない** — 気温プロキシ(`estimate_water_temp`)で回す。`water_temp` は nullable + estimated フラグ。「実測」と偽らない。現場報告(ブログ水温)があればそちらを優先。
- **C&R死亡率は水温連動の保全ゲート** — リリース死亡率は慣用20℃より低い16℃から上昇。C&R区間は「釣れるか」以前に「離した魚が生きるか」で判定を止める（水温≥20℃→NO_GO）。鮎に無い魚種特化レイヤ。
- **判定単位は河川でなく区間(reach)** — 同じ神流川でも上野村(自然流量)と鬼石(下久保ダム放流支配)は真逆。`config.REACHES` が観測点/ダム/営業ルール/釣況源/信頼度を reach_id で紐付ける。
- **精度と網羅の両立 = source_confidence** — 全区間を網羅しつつ、公式ソース実在確認済み(`verified`)の区間だけが確信GOを出す。`参考`区間は物理データ+caveatは出すが確信GOを出さない（`build_verdict` が格下げ）。鮎レーダーで利根川が確信GOを出さなかった思想を区間単位に一般化。
- **濁りはU字応答** — 笹濁り=最好窓、クリア=警戒心で減点、泥濁り=NO_GO。単調減少と誤モデル化しない。
- **放流後経過日数**は管理C&R区間で効く特徴量。0–3日=荒食いブースト／14日超=スレて渋め、を釣り方ヒントに反映（TSIスコアは水温×濁り×日照で算出し放流は直接加点しない＝過剰主張を避ける）。
- **月齢/ソルナーは査読で予測力ゼロ → 使わない**（過剰主張回避）。気圧の絶対値も根拠薄。
- **妄想エンドポイント禁止** — `config` の station ID/URL/ダムID は実在確認済みのみ。未確認のダムID(八ッ場/草木)は空にし「未確認」表示。
- **APIキーはハードコード禁止** — `GEMINI_API_KEY` は環境変数(.env)。`src/llm.py` のみがSDKに触れる。新SDK `google.genai`、model は `gemini-2.5-flash` 固定。
- **Python 3.9ターゲット** — `Optional[X]`/`Union`（`X|Y`不可）、`from __future__ import annotations` 必須（→`.claude/rules/python-common.md`）。

## 構成

```
src/config.py            確定源のみ。REACHES(区間)/RIVER_WATER_LEVEL/JMA_STATIONS/DAM_DISCHARGE/UI_REACHES
src/db.py                SQLiteスキーマ(reach粒度 semantic/stocking/verdict_snapshot, water_temp nullable)
src/llm.py               プロバイダ非依存ラッパ(新SDK google.genai)。差替は本ファイルのみ
src/data_ingestion.py    収集: JMA日次(mean/max/min)/観測点別水位/blog→Gemini(濁り/体感/水温/釣果/放流)/ダム/週間予報
src/dam.py               上流ダム放流量→濁り放流リスク(1h傾き, EUC-JP DspDamData)
src/forecast.py          気象庁週間予報→今後7日のTSI投影(max_tempはC&R午後クローズ判定)
src/guide.py             解説・釣り方ヒント(ルアー/フライ/エサ)・水温帯ガイド・C&R保全・季節カレンダー(純関数)
src/engine/trout_index.py TSI(水温台形×濁りU字×光凸)+ C&R死亡率 + quality_state分類。日ごと独立(積分しない)
src/decision.py          4段ゲート合成(安全→合法/営業→魚の生存(C&R)→釣果)。reach_report がエントリ。build_verdictは純
src/calibration.py       自己監査層: 判定台帳(reach別)+前回比+予実照合(モデルvsブログ体感cond)
scripts/export_html.py   静的HP生成(公開用・区間別・source_confidenceでグルーピング)
scripts/daily_update.py  日次更新: 収集→verdict_snapshot記録→HTML書出し(.env自動読込)
scripts/run_ingest.py    cronエントリ(収集のみ) / scripts/seed_demo.py デモ
app/dashboard.py         Streamlit UI / app/trout_art.py 鱒マスコットSVG / app/river_map.py 川模式マップ
tests/                   network-free(engine/decision/guide/calibration/ingestion/dam/forecast/db/river_map/config)
```

## コマンド

```bash
pip install -r requirements.txt
cp .env.example .env                       # GEMINI_API_KEY
python -m scripts.seed_demo                # デモデータ投入(UI確認用)
streamlit run app/dashboard.py             # ダッシュボード
python -m scripts.run_ingest --no-semantic # ライブ収集(キー無しでも水位/気象/ダムは入る)
python -m scripts.daily_update out.html --full  # 静的HP生成
python -m ruff check --no-cache src/ app/ scripts/  # lint
python -m pytest -q                        # test
```

## 検証ルール（M以上・完了報告前に必須）

`ruff check` と `pytest` を通す(exit 0)。ネットワーク経路(JMA/水位/ダム/semantic)は実行不能なら「未検証」と明記。
TSIパラメータ/水温閾値のデフォルト変更はユーザー確認。本番相当の変更後は `/full-audit`。
詳細な計画/検証基準 → `~/.claude/quality-protocol.md`。

## 現状

エビデンス調査(多軸研究WF 81 findings)→ 鱒エンジン/4段ゲート判定/reach粒度config/収集/HP/テスト 実装。
verified アンカー = **上野村ハコスチ冬季C&R**（公式 ueno-fc.com/winter・infomation）。
参考区間 = 利根川前橋/阪東子持(八ッ場)/渡良瀬桐生(草木)/神流川鬼石(下久保)。
残: 八ッ場/草木のダムID確認・渡良瀬の水位観測点ID確認・水温プロキシの現場較正・毎秋の冬期C&R区間再確認。
```
