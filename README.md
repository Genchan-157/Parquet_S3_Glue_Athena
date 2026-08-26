# Parquet_S3_Glue_Athena

## 開発目的
応えようとした需要
- CSVの山を可視化したい（ファイルサーバー）
- UDPパケットの内容を蓄積して可視化したい（PLCなど）
- 可視化はとりあえず安いAmazon Athenaでやりたい。ODBCドライバは無料。

# プロジェクトの内容
## CSV
CSVファイルのParquet変換。数値型は指定した有効範囲外の値をNULL置換できる。

## Uploader
- 変換した特定の命名規則に沿った名前のついたParquetファイルをAmazon S3にアップロードする
- アップロードしたオブジェクトに指定したタグをつける
- Glue TableがあればにPartitionを追加する

## UDP
- UDPデータを受信して、指定周期でParquetに変換する
- 数値型は指定した有効範囲外の値をNULL置換する
- ParquetファイルをAmazon S3に書き込む
- アップロードしたオブジェクトにタグをつける
- Glue TableがあればにPartitionを追加する
