# Live Trade 設計

> このドキュメントは設計のみ。ユーザーが明示的に許可するまで live 注文は実装・有効化しない。

## 注文状態 State Machine

```
CREATED → SUBMITTED → PARTIAL_FILLED → FILLED
                   → REJECTED
                   → CANCELLED
           ↓
    SUBMIT_FAILED (API エラー)
```

### 状態遷移

| 現在の状態 | イベント | 次の状態 |
|-----------|---------|---------|
| CREATED | API 送信成功 | SUBMITTED |
| CREATED | API 送信失敗 | SUBMIT_FAILED |
| SUBMITTED | 全量約定 | FILLED |
| SUBMITTED | 部分約定 | PARTIAL_FILLED |
| SUBMITTED | 取引所が拒否 | REJECTED |
| SUBMITTED | キャンセル成功 | CANCELLED |
| PARTIAL_FILLED | 残量約定 | FILLED |
| PARTIAL_FILLED | キャンセル成功 | CANCELLED |

### 状態管理

- 全注文状態を SQLite `orders` テーブルに記録
- 状態遷移ごとに `order_events` テーブルにログ
- プロセス再起動時に未完了注文の状態を取引所 API で同期

## API キー管理

- `.env` ファイルに保存、`python-dotenv` で読み込み
- **出金権限は絶対に付けない**
- コードに API キーを直書きしない
- `.env` を `.gitignore` に追加
- `.env` のファイルパーミッションを `600`（Windows: ファイルのプロパティ → セキュリティ → 自分のアカウントのみ読み取り許可）に設定する
- `git-secrets` または `pre-commit` フックで API キーパターンのコミットを機械的に防ぐ
- Live モード起動時に API キーの存在と権限を確認

### APIキーは `SecretStr` で管理（SC-I3）

```python
from pydantic import BaseSettings, SecretStr

class ApiSettings(BaseSettings):
    bitbank_api_key: SecretStr       # str() 変換でマスクされる: "**********"
    bitbank_api_secret: SecretStr

    class Config:
        env_file = ".env"

# 使用例: キー値を取り出す場合は .get_secret_value() を明示的に呼ぶ
api_key = settings.bitbank_api_key.get_secret_value()

# ログ出力でのマスク確認（誤って str() した場合も安全）
logger.info("API settings loaded", api_key=settings.bitbank_api_key)
# → INFO API settings loaded api_key=**********
```

### エラーの sanitize（SC-C2）

API 例外のエラー文字列には認証ヘッダー・APIキー相当の情報が含まれる可能性がある。
DB やログに記録する前に必ず `sanitize_error()` を通す:

```python
import re

_SECRET_PATTERNS = [
    re.compile(r"[0-9a-fA-F]{32,}"),          # API キー・トークン（16進数）
    re.compile(r"Bearer\s+\S+"),                # Bearer トークン
    re.compile(r"(?i)api[_-]?key[=:\s]+\S+"),  # api_key=xxx 形式
]

def sanitize_error(e: Exception) -> str:
    """例外メッセージからセンシティブ情報をマスクする"""
    msg = str(e)
    for pattern in _SECRET_PATTERNS:
        msg = pattern.sub("[REDACTED]", msg)
    return msg

# 使用例（place_order 内）:
except Exception as e:
    order.status = OrderStatus.SUBMIT_FAILED
    order.error = sanitize_error(e)   # ← str(e) ではなく sanitize_error(e)
    raise
```

### 権限チェック（起動時）

```python
async def verify_api_permissions(client: BitbankPrivateClient):
    """Live 起動前に API キーの権限を確認"""
    # 残高取得で API キーの有効性を確認
    assets = await client.get_assets()

    # 出金 API を呼ばない（権限があっても呼ばない）
    # 注文権限の確認は最小注文での dry-run で代替検討
    logger.info("API key verified", assets_count=len(assets))
    # assets オブジェクトをそのままログに渡さない（残高情報の漏洩防止）
```

### 外部アラートのペイロード制御（SC-I4）

外部アラート（メール・Slack 等）を将来追加する場合、送出するペイロードを以下のホワイトリストに限定する:

```python
# OK: 安全なフィールドのみ
alert_payload = {
    "error_code": "DUPLICATE_ORDER",
    "timestamp": str(datetime.utcnow()),
    "pair": "BTC_JPY",
}

# NG: signal オブジェクト全体のシリアライズは禁止
# alert_payload = signal.dict()  ← 資産情報・APIキーが含まれる可能性
```

## 二重発注防止

TOC/TOU レース条件を防ぐため、チェックと挿入を **単一のデータベーストランザクション** で行う（S-C2）。

### 設計原則

- `active_orders` の確認と `CREATED` レコードの挿入を `BEGIN EXCLUSIVE` トランザクション内で実行する
- SQLite の `orders` テーブルに `UNIQUE(pair, direction, status)` 制約を設け、重複挿入を DB レベルで拒否する
  - ただし status が変わると制約が解放されるため、`CREATED` 状態での挿入のみ排他制御の対象とする
- 複数の非同期タスクが同時に `place_order` を呼ぶ設計は禁止。シングルイベントループで順次処理する（後述のイベントループ設計を参照）

```python
async def place_order(self, signal: Signal) -> Order:
    # BEGIN EXCLUSIVE トランザクション内でチェック＋挿入を一括実行（TOC/TOU 防止）
    async with self.db.exclusive_transaction() as conn:
        active = await conn.get_active_orders(signal.pair)
        if any(o.direction == signal.direction for o in active):
            raise DuplicateOrderError(
                f"Active order exists: pair={signal.pair} direction={signal.direction}"
            )
        # プレースホルダー記録（crash recovery: API 呼び出し前に DB に残す）
        order = Order(status=OrderStatus.CREATED, pair=signal.pair, direction=signal.direction, ...)
        await conn.insert_order(order)
    # トランザクション終了後に API 送信（ロック解放）

    try:
        result = await self.exchange.place_order(...)
        order.exchange_order_id = result.order_id
        order.status = OrderStatus.SUBMITTED
    except Exception as e:
        order.status = OrderStatus.SUBMIT_FAILED
        order.error = sanitize_error(e)   # str(e) ではなく sanitize 済み文字列
        raise
    finally:
        await self.db.update_order(order)
    return order
```

## 部分約定の処理（S-I1）

`PARTIAL_FILLED` 状態の完全なライフサイクルを定義する:

### 通常フロー

1. 注文が部分約定 → 状態を `PARTIAL_FILLED` に更新、ポートフォリオに約定済み分を反映
2. `partial_fill_timeout`（デフォルト 30分）ごとにポーリング
3. 残量が追加約定 → `FILLED` に遷移
4. タイムアウト経過後も `PARTIAL_FILLED` のまま → 残量をキャンセル API で取消

### タイムアウト後の処理

```python
# PARTIAL_FILLED のタイムアウト処理
async def handle_partial_fill_timeout(self, order: Order):
    cancelled = await self.cancel_order(order.order_id)
    if cancelled:
        # 部分約定分のみポートフォリオに確定
        order.status = OrderStatus.CANCELLED
        logger.info("Partial fill timeout: remaining cancelled",
                    order_id=order.order_id,
                    filled_size=order.filled_size,
                    remaining_size=order.remaining_size)
    else:
        # キャンセル失敗 → 実際には FILLED になっていた可能性
        latest = await self.exchange.get_order(order.order_id)
        order.status = latest.status
    await self.db.update_order(order)
```

### Kill Switch 発動時の PARTIAL_FILLED 処理

Kill Switch 発動時に `PARTIAL_FILLED` の注文が存在する場合:
1. まず残量をキャンセル（成行クローズより先に実行）
2. キャンセル成功後に部分約定分を成行でクローズ
3. キャンセル失敗の場合は `MANUAL_INTERVENTION_REQUIRED` 状態に遷移（risk_policy.md 参照）

## 残高同期

- 各サイクルの開始時に取引所 API から残高を取得
- ローカル DB の残高と比較
- 乖離が閾値（0.1%）を超えた場合:
  - ログに警告を記録
  - 取引所の値をマスターとして同期
  - 乖離理由を調査（手動取引、手数料計算誤差、等）

## キャンセル処理

```python
async def cancel_order(self, order_id: str) -> bool:
    try:
        await self.exchange.cancel_order(order_id)
        await self.db.update_order_status(order_id, OrderStatus.CANCELLED)
        return True
    except OrderNotFoundError:
        # 既に約定済みの可能性 → 状態を同期
        status = await self.exchange.get_order(order_id)
        await self.db.update_order_status(order_id, status)
        return False
```

## Kill Switch（Live 版）

Paper trade の Kill Switch に加え:

1. 全未約定注文を取引所 API でキャンセル
2. 全ポジションを成行で決済
3. キャンセル・決済の成否を確認
4. 失敗した場合はリトライ（最大3回）
5. それでも失敗した場合は手動対応を促すアラート

## 手動停止手順

1. `config.yaml` の `kill_switch.active` を `true` に変更 → 次サイクルで停止
2. **緊急時**: プロセスを `Ctrl+C` / `kill` で停止
3. 取引所の Web UI で未約定注文を確認・キャンセル
4. 取引所の Web UI で保有ポジションを確認
5. `logs/` で最終状態を確認
6. 再開する場合は停止原因を調査してから `kill_switch.override` を `true` に設定

## 非同期処理のイベントループ設計（S-I2）

### アーキテクチャ: シングルループ + Queue

```
┌─────────────────────────────────────────────────────┐
│  Main Event Loop（asyncio.run で単一ループ）         │
│                                                     │
│  ┌─────────────┐    asyncio.Queue    ┌───────────┐  │
│  │ DataFetcher │ ──── signal_q ───→  │ Execution │  │
│  │  (Producer) │                     │ (Consumer) │  │
│  └─────────────┘                     └───────────┘  │
│         ↓                                  ↓        │
│  ┌─────────────┐                   ┌────────────┐   │
│  │  Strategy   │                   │ RiskManager│   │
│  │  Regime     │                   │ KillSwitch │   │
│  └─────────────┘                   └────────────┘   │
└─────────────────────────────────────────────────────┘
```

**設計ルール**:
- 複数の `asyncio.Task` を同時実行しない。`signal_q` を経由した Producer/Consumer パターンで順次処理する
- HTTP 注文（`place_order`）は Consumer タスク内で実行するため、価格データ受信ループをブロックしない
- WebSocket 価格フィードは将来的に追加可能だが、MVP は 1hour ごとの REST API ポーリングのみ
- 並行実行が必要になった場合は `asyncio.Queue` を拡張し、Consumer を増やす設計（現時点では Consumer = 1）

### 1時間サイクルの処理順序

```
毎時 0分:
  1. [Fetch]    ticker / depth / candlestick を REST で取得
  2. [State]    市場環境チェック（スプレッド、板厚、ボラティリティ）
  3. [Risk]     Circuit Breaker チェック（残高ベースの動的閾値）
  4. [Regime]   RegimeDetector でレジーム判定
  5. [Position] レジーム変化に伴うポジションクローズ（TREND_DOWN / HIGH_VOL）
  6. [Signal]   シグナル生成（有効戦略のみ）
  7. [Execute]  Paper / Live Executor に signal_q 経由で渡す
  8. [Persist]  ポートフォリオ状態・監査ログを保存
```

## 設計上の注意

- Live executor は **環境変数 `TRADING_MODE=live` + `--confirm-live` CLI フラグの両方** がある場合のみ有効化（master_design.md の二段階ゲート参照）
- `config.yaml` の `mode: live` 単独では live 起動しない
- テストでは mock exchange を使い、実際の API を呼ばない
- Live executor のコードは存在するが、デフォルトでは import されない
