#!/usr/bin/env python3
"""
公開ガード: いま GitHub Pages に公開してよいかを判定する。

判定材料は publish_meta.json（直近の公開が Garmin 反映済みかどうか・いつ公開したか）。
Garmin 回復・負荷データはローカル Mac にしかないため、Garmin 反映済みの内容を
Garmin 無しの内容で上書き（劣化）させないためのガード。

exit 0 = 公開してよい / exit 1 = 公開を見送る（デファー）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

MIN_GENERAL_GRACE_HOURS = 2    # 直後の重複公開を避ける
GARMIN_GRACE_HOURS = 20        # Garmin 反映済みコンテンツを保護する猶予
HARD_CEILING_HOURS = 48        # これを超えたら無条件で公開（鮮度の安全網）


def _parse_bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def decide(meta_path: str, force: bool) -> tuple[bool, str]:
    if force:
        return True, "FORCE_PUBLISH=true — ガードを無視して公開"

    if not os.path.exists(meta_path):
        return True, f"{meta_path} が存在しない — 初回扱いで公開"

    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        published_at = datetime.fromisoformat(meta["published_at"])
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        return True, f"{meta_path} の読み込みに失敗（{e}） — フェイルセーフで公開"

    age_hours = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600.0
    is_garmin = bool(meta.get("garmin"))

    if age_hours >= HARD_CEILING_HOURS:
        return True, f"前回公開から{age_hours:.1f}時間経過（上限{HARD_CEILING_HOURS}h超）— 安全網として公開"

    if age_hours < MIN_GENERAL_GRACE_HOURS:
        return False, f"前回公開から{age_hours:.1f}時間しか経過していない（猶予{MIN_GENERAL_GRACE_HOURS}h）— 公開見送り"

    if is_garmin and age_hours < GARMIN_GRACE_HOURS:
        return False, f"Garmin反映済みの内容が{age_hours:.1f}時間前と新しい（猶予{GARMIN_GRACE_HOURS}h）— 公開見送り"

    return True, f"前回公開から{age_hours:.1f}時間経過、Garmin={is_garmin} — 公開OK"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meta-file", default="publish_meta.json")
    args = parser.parse_args()

    force = _parse_bool_env("FORCE_PUBLISH")
    should_publish, reason = decide(args.meta_file, force)
    print(reason, file=sys.stderr)
    return 0 if should_publish else 1


if __name__ == "__main__":
    sys.exit(main())
