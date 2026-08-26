# UDP_Receive.py

# 開発目的
UDPで受信したデータを可視化したい（PLC）

## 手段
- UDPフレームをParquet変換する
- Amazon S3にHive形式でアップロードする
- Glue TableにPartPartitonを追加する。Tableそのものの作成はCrawlerで実施する前提

## 特徴
- PLCなどからUDPフレームをバイナリで送信してもらう前提のため、相手側機器を選びません
- UDP一方通行なので、PLC -> ルーター -> Raspberry Piという構成でも動作します。
  ルーターを中間に挟むことで、Raspberry Piをハックされた時のPLCのセキュリティが向上します。

## （参考）可視化の方法
Athenaにアクセスできれば、どんな方法も適用可能。
以下に例を示す。
- Atehna ODBCドライバー＋Power BI Desktop
- Athena ODBCドライバー＋Excel
- Athenaで直接クエリを実行
- S3のデータだけを他のRedshiftなど他のDWHにロードする。データがParqeutなので、CSVと異なりスキーマエラーが出ない。

## （テスト用）util_UDP_Send.py
settings.jsonの設定に沿ってダミーデータを送信します。

# 制限事項

## 列名
Amazon Ahtenaの制約 <b>^[a-z][0-9a-z_]*$</b> に従います。

## 列型
列型もAmazon Athenaの制約に従います。また、PLCの一般的な制約に従います。
- 基本型のみを扱います。BINARY, ARRAY, MAP, STRUCTは対応しません
- 文字列はASCII, Shift-JIS（cp932）、UTF-16LEを想定しています。UTF-8はPLCで扱いづらいので想定外です
- unsigned型は扱えません
- timezone offsetは扱えず、時刻はUTCが前提です。夏時間が問題になる、UTCを利用することを推奨します。タイムゾーンはAthena側でViewを作れば追加可能です。
- データ型は以下の表に従います。Glue Crawlerがstring判定することがあるので、複雑な型はstringで代用する前提で実装していません。

| Athena | PyArrow | 備考 |
| :--- | :--- | :--- |
| BOOLEAN | bool_() |  |
| TYNYINT | int8() |  |
| SMALLINT | int16() |  |
| INT | int32() |  |
| BIGINT | int64() |  |
| FLOAT | float32() |  |
| DOUBLE | float64() |  |
| CHAR | N/A | PyArrowに固定長文字列がない |
| STRING | string() |  |
| VARCHAR | N/A | PyArrowに固定長文字列がない |
| DATE | date32() | 日付のみの利用を想定 |
| TIMESTAMP | timestamp() | UTC時刻を想定 |

## プロセス数
受信用と送信用の2プロセスでダブルバッファリングしています。
Receiverを増やすことは可能ですが、利用者にて処理が溢れないように適宜調整願います。
Raspberry Pi Zero2Wのテストでは、timestamp1列＋float 2000列を10Hzで処理できました。

## 受信データのバイトオーダー
Little Endianのみ対応です。

# IAM権限について

## アップロードするIAMユーザーの権限
IAMフォルダのuploader.jsonの設定

## Glue Crawlerの権限
Crawler作成時にロールを作成できるので、フォルダへのアクセス権をIAMフォルダのGlueCrawler.jsonのように追加する

# 設定ファイルsetting.jsonについて
サンプルの内容の説明です

## FileComment
コメント

## Receiver_IP
自機のIPアドレス

## localTimezone
localTime列の時差指定。+09:00などPythonのTimezone指定を行うこと

## HttpProxy
hostが""だと、Proxyを使用しない設定

## Receiers
複数ポートを開いて待受は可能。ただし、全ReceiverでProcessを共用しているので、
多数のReceiverに高頻度に処理をさせた場合の処理落ちに注意。

### name
名称

## UDP_Port
ポート番号を10進数で指定

## output
- intervalMinutes
送信間隔。少なくすると停電に強くなりメモリも節約でき、リアルタイム処理に近づくが、クラウド側の結合処理が実質必須になる。
長くするとその逆。
- MaxRowCountPerFile
受信バッファの行数。1秒で1分なら60行になるが、ジッターを考慮して多めに確保すること。
- suffix
ファイル名の拡張子の前に付ける文字。ファイルだけダウンロードした時になんのファイルか識別するために文字列を指定できる。
- fileTimezone
ファイル名やGlue Partitionの区別をする基準。"UTC"ならUTCでそれ以外ならPython実行環境の現在時刻。

## AWS

### S3FileSystem
pyarrow.fs.S3FileSystem 構築時に渡す引数

### BucektName
S3バケット名

### ObjectKeyPrefix
バケット名直下のプリフィックス。/で終わること。

### Tagging
コスト配分タグなどのタグ付け。プロジェクト単位でストレージコストを計算したい時などに利用可能。

### Pratiton
Glue TableのPartiton登録。TableそのものはGlue Crawlerなどで作成する想定。

- Database
Database名

- Table
Table名

- Keys
Partiton Keyのkeyとvalue（strftimeで読み替える）

### field_repeat
複数のレコードを1度に送信することで、UDPパケットの通信回数を減らして、高頻度データを格納したい時に利用する。
通常は1。

### fields
受信フレームの定義
- type
列型。
    - utctime
      Python実行環境の時刻
    - datetime
      YYYYMMDDhhmmsswwの10bytesを読み込んでtimestampにする（MELSEC-QのDATERD命令相当）
    - datetime_ms
      YYYYMMDDhhmmsswwffの11bytesを読み込んでtimestampにする（MELSEC-QのDATERD命令相当）
    - bool16, bool32
      boolフラグ列。nameが配列になっていて、""の列は出力しない。配列長は16または32が必要。
    - int16, int32, int64, float32, float64
      整数型。min未満またはmax超過でnullに置換される。
    - ascii, shift_jis, utf_16_le
      文字列型。bytesでフィールド長を指定する
- name
  列名。bool16, bool32では配列で指定する。
- min, max
  数値型の最小値と最大値。nullを指定するか指定そのものを削除すればチェックしない。


