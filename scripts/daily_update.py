"""日次更新ランナー — 収集 → 判定スナップショット記録 → 静的HP再生成 を1コマンドで。

  python -m scripts.daily_update [出力パス] [--no-semantic]

- GEMINI_API_KEY があれば semantic（ブログ→Gemini）込み、無ければ物理データのみで走り、
  その旨を正直にログする（黙って欠けない）。
- .env があれば読み込む（ローカル cron/launchd から export 無しで動かすため）。
- 各河川の判定を verdict_snapshot に記録してから HTML を書き出す。台帳が
  「前回更新からの変化」と「予実照合」の元データになる。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.export_html import build_html  # noqa: E402
from src import calibration, config, data_ingestion, db, decision  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def load_dotenv(path: Path) -> None:
    """最小の .env 読み込み（KEY=VALUE 行のみ・既存の環境変数は上書きしない）。

    `export KEY=...` 形式と、未クォート値の行内コメント（` # ...`）も正しく扱う —
    ここを黙って取りこぼすと semantic が「キー未設定」として静かにスキップされるため。
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip()
        if len(val) >= 2 and val[0] in "\"'" and val.endswith(val[0]):
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> None:
    ap = argparse.ArgumentParser(description="niji_gunma daily update (ingest+snapshot+HTML)")
    ap.add_argument("out", nargs="?", default=None, help="出力HTMLパス")
    ap.add_argument("--no-semantic", action="store_true",
                    help="LLM経路を強制スキップ（キーがあっても使わない）")
    ap.add_argument("--full", action="store_true",
                    help="<!doctype html> 完全文書で出力（GitHub Pages 等の直接配信用。"
                         "Artifact 用は骨格が二重になるため付けない）")
    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    with_semantic = not args.no_semantic and bool(os.environ.get("GEMINI_API_KEY"))
    if not with_semantic:
        print("[Env]      GEMINI_API_KEY 無し（または --no-semantic）→ ブログ照合はスキップ。"
              "物理データ（気象/水位/ダム/予報）のみ更新")

    data_ingestion.run(with_semantic=with_semantic)

    run_date = dt.datetime.now(JST).date().isoformat()
    conn = db.connect()
    try:
        demo = conn.execute(
            "SELECT (SELECT COUNT(*) FROM weather_data WHERE source='seed_demo')"
            " + (SELECT COUNT(*) FROM semantic_field_logs WHERE source_name='seed_demo')"
            " + (SELECT COUNT(*) FROM river_physical_data WHERE source='seed_demo')"
            " + (SELECT COUNT(*) FROM dam_data WHERE source='seed_demo')"
            " + (SELECT COUNT(*) FROM forecast_data WHERE source='seed_demo')").fetchone()[0]
        if demo:
            print(f"[WARN]     デモデータ{demo}行がDBに残存 — 公開ページに架空の判定が混入する。"
                  "source='seed_demo' 行を削除してから公開すること")
        for reach_id in config.UI_REACHES:
            # today= を JST で明示 — CI(UTC) では date.today() が JST より1日過去になり、
            # 鮮度窓と台帳 run_date が恒常的にズレるため（ローカルでは再現しない罠）。
            v = decision.reach_report(conn, reach_id, today=run_date)
            if v.as_of is None:
                print(f"[Snapshot] {reach_id} 実データなし — 記録スキップ")
                continue
            calibration.save_snapshot(
                conn, calibration.snapshot_from_verdict(v, run_date))
            conn.commit()
            print(f"[Snapshot] {reach_id} {run_date}: {v.level} TSI{v.tsi or 0:.0f} "
                  f"conf{v.confidence:.2f}")
        out = Path(args.out) if args.out else db.DB_PATH.parent / "niji_radar.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        # run_date を渡して snapshot と同一日付で描画（深夜跨ぎで「前回=今日」になる乖離を防ぐ）
        out.write_text(build_html(conn, run_date=run_date, full_document=args.full),
                       encoding="utf-8")
        print(f"[HTML]     wrote {out} ({out.stat().st_size} bytes)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
