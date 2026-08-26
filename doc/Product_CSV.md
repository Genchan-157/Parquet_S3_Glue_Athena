# ParquetBatchConverter.py

## 開発目的
応えようとした需要
- CSVの山を可視化したい（ファイルサーバー）

### 手段
- CSVをParquet変換する

# 制限事項

## 入力ファイル
CSVファイルに対応しています。
TSVに対応したい場合はWriterクラスのdef CsvToTsv()を編集してください。

## 列名
Amazon Ahtenaの制約 <b>^[a-z][0-9a-z_]*$</b> に従います。

## 列型
列型もAmazon Athenaの制約に従います。
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
| DECIMAL | decimal128() | Athenaが38桁まで |
| CHAR | N/A | PyArrowに固定長文字列がない |
| STRING | string() |  |
| VARCHAR | N/A | PyArrowに固定長文字列がない |
| BINARY | binary() | 未実装 |
| DATE | date32() | 日付のみの利用を想定 |
| TIMESTAMP | timestamp() | UTC時刻を想定 |
| ARRAY | string() | Glue Crawlerが処理できないのでString |
| MAP | string() | Glue Crawlerが処理できないのでString |
| STRUCT | string() | Glue Crawlerが処理できないのでString |

# 設定ファイルconverter.jsonについて

## Directories
Input, Output, Convertedの相対パス

## input
### encoding
読み込むファイルの文字エンコード。utf_8やcp932（shift_jis）など、Pythonの文字エンコードを表す文字列
### format
"CSV"ならカンマ区切り、"TSV"ならタブ区切り
### DateTimeFormat
時刻文字列の読み取り
- DateTimeDelimiter
日付と時刻の区切り
- DateDelimiter
年/月/日の区切り
- DateOrder
年月日の並び。ymd, dmy, mdyを選択可能。
### timezone
読み込んだ時刻のUTCからの時差。JSTなら"+09:00"
### headerRows
ヘッダの行数。0なら最初からデータ業とみなす。
## AWS
- GlueFields
trueでGlueCrawlerが処理できない列をstringと定義する。
## fields
列定義
- name
列名
- type
None, string, bool, decimal, int{8,16,32,64}{_f,_s}, float{32,64}{_f,_s},date{_s}, time{_s}localTime{_s}, localTimeIso, utcTime, list, map, struct, special{00,01,02,03,04}のいずれかを指定。

- typeに_fを付けると上下限指定外でNULL置換
- typeに_sを付けると上下限指定外でNULL置換した上で文字列として「（列名）_str」と命名されたに出力する。
- min, max
上下限値の指定
- precision, scale
dedimal128型のパラメータ
- data_type list型のデータ型。
- key_type, item_type map型のkey, itemのデータ型
- fields struct型のフィールド定義
