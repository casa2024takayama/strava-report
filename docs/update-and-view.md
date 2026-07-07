# レポートを最新にして表示する（トラブル回避メモ）

Mac でレポートを更新・確認するときの手順と、よくある「更新したのに反映されない」の原因・回避策。

## いちばん簡単：1コマンド

```bash
cd ~/Projects/strava-report
bash refresh.sh
```

`refresh.sh` が以下をまとめて安全に実行する：

1. 現在のブランチをリモート最新へ同期（`git fetch` + `git reset --hard`。生成物のみ破棄で安全）
2. 動いている古いサーバーを確実に停止（launchd / ポート 8766）
3. サーバーを起動（起動時に新コードで HTML を焼き直す）

起動後、ブラウザでページを開く。**キャッシュ対策はサーバー側で入れてある**（`Cache-Control: no-store`）ので、通常のリロードで最新が表示される。

## 「更新したのに反映されない」3つの原因

過去にハマった典型。`refresh.sh` はこれらを全部回避する。

1. **ブランチが違う / `git pull` が効かない**
   - 別ブランチ（例 `main`）のままだと古いコードが動く。
   - フィーチャーブランチは upstream 未設定だと `git pull` が「Already up to date」と言って**実際は更新されない**ことがある。
   - 確認: `git log --oneline -1` が期待するコミットか。
   - 手動で直す場合:
     ```bash
     git fetch origin <branch>
     git reset --hard FETCH_HEAD        # 生成物のみ破棄・安全
     git branch --set-upstream-to=origin/<branch>   # 以後は git pull でOK
     ```

2. **古いサーバーが動いたまま**
   - `serve_report.py` は**起動時**に HTML を焼き直すが、既にサーバーが動いていると「既に起動中」と判定して**焼き直さずに古いページを開くだけ**になる。
   - 手動で直す場合（新しく起動する前に必ず止める）:
     ```bash
     launchctl unload ~/Library/LaunchAgents/com.casa.strava-report-server.plist 2>/dev/null
     lsof -ti :8766 | xargs kill 2>/dev/null
     ```

3. **ブラウザキャッシュ**
   - 古い `index.html` がキャッシュされて表示される。
   - サーバー側で `Cache-Control: no-store` を返すようにしたので通常は不要だが、念のため PC は `Cmd+Shift+R`、スマホはタブを閉じて開き直す（or プライベートタブ）。

## 補足

- 4タブ改修が **`main` にマージされた後は、ブランチを気にせず `git pull` だけ**で済む（`refresh.sh` は引き続き「止めて→同期→焼き直して起動」の時短に使える）。
- `git reset --hard` は、このリポジトリでは生成物（`index.html` / `20*.html` / `publish_meta.json` / `plan_*.json`）を捨てるだけなので安全。手で編集した未コミットの変更がある場合、`refresh.sh` は中断して知らせる。
