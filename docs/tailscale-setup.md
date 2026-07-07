# Tailscale経由でスマホからローカルレポートサーバーにアクセスする

GitHub Pages（`publish.sh`/Actionsが公開する静的コピー）とは別に、Mac上で動く
ローカル開発サーバー（`serve_report.py`）— Garmin反映済みの詳細な内容や
「データ更新」「AI評価」「Garmin取得」ボタンが使える版 — に、スマホから
Tailscale（プライベートメッシュVPN）経由で直接アクセスするための手順。

この手順はMac/スマホの実機操作が必要なため、リモートのコーディング環境からは
実行できない。ユーザー自身で一度だけ行う。

## 1. 両方の端末をTailscaleに参加させる

1. Macに [Tailscale](https://tailscale.com/) をインストールし、ログインする。
2. スマホにもTailscaleアプリをインストールし、**同じアカウント（同じtailnet）**でログインする。
3. Macのターミナルで、割り当てられたTailscale IPを確認する:
   ```
   tailscale ip -4
   ```
   `100.x.y.z` の形式のアドレスが表示されるはずです。

## 2. リポジトリの初回セットアップ（.venv / .env の作成）

まだ一度も実行していない場合、リポジトリのディレクトリで:
```
bash update_report.sh
```
を実行し、`.venv` を作成し、レポートを一度生成しておく（Ctrl+Cで終了して構いません）。

## 3. サーバーをTailscale経由で起動する

手動でその都度起動する場合:
```
REPORT_SERVER_HOST=auto bash -c 'cd /path/to/strava-report && .venv/bin/python3 serve_report.py --open'
```
起動時に表示されるURL（`http://100.x.y.z:8766/index.html`）がスマホからアクセスするURLです。

初回起動時、`.env`に`REPORT_SERVER_TOKEN`が自動生成されます（API保護用の秘密トークン）。
このトークンはHTML生成時にページへ埋め込まれるため、通常は意識する必要はありません。

## 4. Mac起動時に自動で立ち上がるようにする（launchd, 任意）

常に手動起動するのが面倒な場合、`launchd/com.casa.strava-report-server.plist` を使う。

1. `launchd/com.casa.strava-report-server.plist` 内の `/REPLACE/WITH/YOUR/REPO/PATH` を、
   実際にこのリポジトリをcloneしたパスに書き換える（2箇所）。
2. `~/Library/LaunchAgents/` にコピー:
   ```
   cp launchd/com.casa.strava-report-server.plist ~/Library/LaunchAgents/
   ```
3. 読み込んで起動:
   ```
   launchctl load ~/Library/LaunchAgents/com.casa.strava-report-server.plist
   ```
   （新しいmacOSでは `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.casa.strava-report-server.plist` でも可）
4. ログは `/tmp/strava-report-server.log` に出力される。
5. 停止したい場合:
   ```
   launchctl unload ~/Library/LaunchAgents/com.casa.strava-report-server.plist
   ```

launchdで起動すると、ログイン時に自動でサーバーが立ち上がり、クラッシュ時も自動再起動する。
Macがスリープ/シャットダウンしている間はもちろんアクセスできない。

## 5. スマホからアクセスする

スマホのTailscaleアプリが接続された状態で、ブラウザから:
```
http://<Macのtailscale-ip>:8766/index.html
```
を開く。「データ更新」「AI評価」「Garmin取得」ボタンも動作するはずです。

## セキュリティに関する注意

- `serve_report.py`は`REPORT_SERVER_HOST=auto`のとき、Tailscaleの`100.x.y.z`アドレスにのみ
  バインドします（`0.0.0.0`ではないので、自宅Wi-Fi等の同一LAN上の他デバイスからは見えません）。
- `/api/update`・`/api/coach`・`/api/garmin`・`/api/status`はトークン認証で保護されていますが、
  これは「誤って外部に晒された場合の多層防御」であり、tailnetに参加できる端末（＝あなたが
  許可した端末）は引き続き正規にアクセスできます。tailnetへの招待は慎重に。
- Garmin回復データなど個人の健康情報を含むローカル版を開放するため、Tailscaleのtailnetには
  信頼できる自分の端末のみを参加させてください。
