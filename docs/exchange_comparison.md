# 取引所 API 比較

調査日: 2026-04-14

## 比較対象

日本の金融庁（関東財務局）に登録済みの暗号資産交換業者のうち、API を提供する以下4社を比較。

| 取引所 | 登録番号 | 公式URL |
|--------|---------|---------|
| bitbank | 関東財務局 第00004号 | https://bitbank.cc/ |
| GMOコイン | 関東財務局 第00006号 | https://coin.z.com/ |
| bitFlyer | 関東財務局 第00003号 | https://bitflyer.com/ |
| Coincheck | 関東財務局 第00014号 | https://coincheck.com/ |

## API 機能比較

| 機能 | bitbank | GMOコイン | bitFlyer | Coincheck |
|------|---------|-----------|----------|-----------|
| 現物取引所 API | ○ | ○ | ○ | ○ |
| Public API | ○ | ○ | ○ | ○ |
| Private API | ○ | ○ | ○ | ○ |
| WebSocket | ○ (Stream) | ○ | ○ (Realtime) | × |
| OHLCV 取得 | ○ (1min〜1month) | ○ | × (自前集計必要) | × |
| 板情報取得 | ○ | ○ | ○ | ○ |
| 約定履歴取得 | ○ (日付指定可) | ○ | ○ | ○ |
| 残高取得 | ○ | ○ | ○ | ○ |
| 注文 | ○ | ○ | ○ | ○ |
| キャンセル | ○ (単体/複数) | ○ | ○ | ○ |
| 約定照会 | ○ | ○ | ○ | ○ |

## BTC/JPY 取引仕様比較

| 項目 | bitbank | GMOコイン | bitFlyer | Coincheck |
|------|---------|-----------|----------|-----------|
| Maker 手数料 | 0% | -0.01% (報酬) | 0.15%〜0.01% (出来高連動) | 無料 |
| Taker 手数料 | 0.1% | 0.05% | 0.15%〜0.01% | 無料 |
| 最小注文数量 | 0.0001 BTC | 0.00001 BTC | 0.001 BTC | 0.005 BTC |
| 価格刻み | 1 JPY | 1 JPY | 1 JPY | — |
| 数量刻み | 0.0001 BTC | 0.00001 BTC | 0.001 BTC | 0.005 BTC |
| 注文タイプ | limit, market, stop, stop_limit, take_profit, stop_loss | limit, market, stop | limit, market | limit, market |

※ bitbank の BTC/JPY 手数料は 2026年2月2日に改定（Maker: -0.02% → 0%, Taker: 0.12% → 0.1%）

## API 技術仕様比較

### bitbank

- **API ドキュメント**: https://github.com/bitbankinc/bitbank-api-docs（GitHub 公開、日英対応）
- **Public API Base URL**: `https://public.bitbank.cc`
- **認証方式**: HMAC-SHA256（ACCESS-TIME-WINDOW 方式 / ACCESS-NONCE 方式の2種）
- **レート制限**: GET 10回/秒, POST(注文等) 6回/秒, 超過時 HTTP 429
- **Public API エンドポイント**:
  - `GET /{pair}/ticker` — ティッカー
  - `GET /{pair}/depth` — 板情報
  - `GET /{pair}/transactions/{YYYYMMDD}` — 約定履歴（日付指定可）
  - `GET /{pair}/candlestick/{candle-type}/{YYYY}` — OHLCV
- **Candlestick 時間足**: 1min, 5min, 15min, 30min, 1hour, 4hour, 8hour, 12hour, 1day, 1week, 1month
- **Private API エンドポイント**:
  - `GET /user/assets` — 残高
  - `POST /user/spot/order` — 新規注文
  - `POST /user/spot/cancel_order` — キャンセル
  - `POST /user/spot/cancel_orders` — 複数キャンセル
  - `GET /user/spot/orders_open` — 未約定注文一覧
  - `GET /user/trades` — 約定照会

### GMOコイン

- **API ドキュメント**: https://api.coin.z.com/docs/
- **認証方式**: HMAC-SHA256
- **レート制限**: Private API 10回/秒（取引高1億円以上: 20回/秒）
- **Public API**: ticker, orderbooks, trades, klines
- **Private API**: 注文, 残高, 約定照会, キャンセル

### bitFlyer

- **API ドキュメント**: https://lightning.bitflyer.com/docs
- **認証方式**: HMAC-SHA256
- **レート制限**: Private ~200回/分, IP ~500回/分, 超過時1時間制限
- **OHLCV API**: なし（自前でティック/約定から集計が必要）
- **注意**: 0.01 BTC以下の大量注文は10回/分に制限される場合あり

### Coincheck

- **API ドキュメント**: https://coincheck.com/documents/exchange/api
- **WebSocket**: なし
- **レート制限**: 公式ドキュメントに明記なし
- **最小注文数量**: 0.005 BTC（BTC=1500万JPY 想定で約75,000円）

## 手数料・コスト比較

| 項目 | bitbank | GMOコイン | bitFlyer | Coincheck |
|------|---------|-----------|----------|-----------|
| JPY 入金 | 無料 | 即時入金無料 | 無料〜330円 | 即時入金770〜1,018円 |
| JPY 出金 | 550〜770円 | 無料(大口400円) | 220〜770円 | 407円 |
| BTC 送付 | 0.0006 BTC | **無料** | 0.0004 BTC | 0.0005 BTC |

## 10万円運用との相性分析

BTC = 1,500万JPY 想定での試算:

| 観点 | bitbank | GMOコイン | bitFlyer | Coincheck |
|------|---------|-----------|----------|-----------|
| 最小取引額 | ~1,500円 | ~150円 | ~15,000円 | ~75,000円 |
| 1万円取引時の手数料 | M: 0円 / T: 10円 | M: -1円 / T: 5円 | M/T: 15〜150円 | 0円 |
| 細かいポジション調整 | △ (0.0001刻み) | ◎ (0.00001刻み) | × (0.001刻み) | × (0.005刻み) |
| バックテストデータ取得 | ◎ | ○ | × | × |
| API ドキュメント品質 | ◎ | ○ | ○ | △ |

## 選定結果

### MVP 取引所: **bitbank**

**選定理由:**

1. **API ドキュメントの品質が最も高い** — GitHub で公開、日本語/英語対応、エンドポイントが明確で実装が容易
2. **OHLCV Candlestick API が充実** — 1min〜1month の11種類の時間足を日付/年指定で取得可能。バックテスト用の過去データ収集が最も容易
3. **注文タイプが豊富** — limit, market に加え stop, stop_limit, take_profit, stop_loss を API レベルでサポート。将来の live trade 設計に有利
4. **Taker 手数料 0.1% は MVP で十分許容** — 10万円運用で1取引1万円なら10円。手数料耐性の検証対象として現実的な値
5. **レート制限が明確** — GET 10回/秒、POST 6回/秒と公式に明示。設計・実装時の考慮が容易
6. **ユーザーがアカウントを保有済み**

### GMOコインを選ばなかった理由

- 手数料面では GMOコインが優位（Maker -0.01%, Taker 0.05%）
- ただし OHLCV の過去データ取得の容易さで bitbank が上回る
- API ドキュメントへのアクセスに一部困難あり
- **将来の取引所追加候補**として Exchange adapter 設計に含める

### bitFlyer を除外した理由

- OHLCV API がなく、過去データ取得に大きな手間がかかる
- 手数料が出来高連動で、10万円の小資金運用では最も不利（0.15%）
- 最小注文数量 0.001 BTC（約15,000円）は10万円運用に対して粒度が粗い

### Coincheck を除外した理由

- WebSocket 未対応で、リアルタイムデータ取得に制約
- API ドキュメントが最低限
- 最小注文数量 0.005 BTC（約75,000円）は10万円運用ではほぼ一括投資になり、戦略の検証が困難
- 即時入金手数料が高い（770〜1,018円）

## 情報源

- bitbank 手数料: https://bitbank.cc/guide/fee
- bitbank 取扱ペア: https://bitbank.cc/guide/pair
- bitbank API docs: https://github.com/bitbankinc/bitbank-api-docs
- GMOコイン手数料: https://coin.z.com/jp/corp/guide/fees/
- GMOコイン API: https://api.coin.z.com/docs/
- bitFlyer 手数料: https://bitflyer.com/ja-jp/s/commission
- bitFlyer API: https://lightning.bitflyer.com/docs
- Coincheck 手数料: https://coincheck.com/info/fee
- Coincheck API: https://coincheck.com/documents/exchange/api
