# MiniMax H3（ローカル動画生成）の起動ガイド

MiniMax H3 は 2026年8月にオープンウェイト公開された動画（音声つき）生成モデルで、
ComfyUI がネイティブ対応しています（ComfyUI **0.30.0 以降**、専用カスタムノード不要）。
このリポジトリの `minimax_h3.sh` を使うと、起動〜ブラウザ表示までを1コマンドで行えます。

## ふだんの起動

```bash
bash minimax_h3.sh
```

- ComfyUI の場所を自動検出（`~/ComfyUI` など。見つからない場合は
  `COMFYUI_DIR=/path/to/ComfyUI bash minimax_h3.sh` で指定）
- すでに起動済みならブラウザを開くだけ（二重起動しない）
- 起動後、ComfyUI の **Template Library > Video > MiniMax H3** から
  T2V / I2V / R2V のワークフローを開けます

ComfyUI 本体の更新もまとめて行う場合:

```bash
bash minimax_h3.sh --update
```

## 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `COMFYUI_DIR` | 自動検出 | ComfyUI のインストール先 |
| `COMFYUI_PORT` | `8188` | サーバーポート |
| `COMFYUI_ARGS` | なし | ComfyUI への追加引数（例 `--force-fp16`） |

## 初回セットアップ（モデル配置）

いちばん簡単なのは、起動後にテンプレート（Template Library > Video > MiniMax H3）を
開くこと。不足モデルのダウンロードをポップアップで案内してくれます。

手動で配置する場合は [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)
から取得して `ComfyUI/models/` 以下に置きます:

| 配置先 | ファイル（例・省VRAM構成） |
|---|---|
| `models/diffusion_models/` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors`（I2V/T2V 用。参照動画を使うなら `ref2va` 版） |
| `models/text_encoders/` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| `models/vae/` | `minimax_h3_video_vae_fp16.safetensors` と `minimax_h3_audio_vae_fp32.safetensors` |

bf16 など高精度版のバリアントも同リポジトリにあります。12GB 程度の VRAM でも
int8 / 量子化版なら現実的な時間で生成できます。

## トラブルシューティング

- **起動に失敗する** — ログは `/tmp/comfyui-minimax-h3.log`（スクリプトが末尾を表示します）
- **テンプレートに MiniMax H3 が出ない** — ComfyUI が古い可能性。`bash minimax_h3.sh --update`
- **ポート競合** — `COMFYUI_PORT=8189 bash minimax_h3.sh`
