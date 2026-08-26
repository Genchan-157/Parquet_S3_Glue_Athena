# S3BatchUploader.py

## 開発目的
応えようとした需要
- CSVの山を可視化したい（ファイルサーバー）

## 手段
- Amazon S3にHive形式でParquetファイルをアップロードする。Parquet変換は事前に済ませてある前提
- Glue TableにPartPartitonを追加する。Tableそのものの作成はCrawlerで実施する前提

## （参考）可視化の方法
Athenaにアクセスできれば、どんな方法も適用可能。
以下に例を示す。
- Atehna ODBCドライバー＋Power BI Desktop
- Athena ODBCドライバー＋Excel
- Athenaで直接クエリを実行
- S3のデータだけを他のRedshiftなど他のDWHにロードする。データがParqeutなので、CSVと異なりスキーマエラーが出ない。

# 制限事項

### ファイル名
YYYYMMDD_hhmmss*.parquet形式のファイル名に対応しています。

# IAM権限について

## アップロードするIAMユーザーの権限
IAMフォルダのuploader.jsonの設定

## Glue Crawlerの権限
Crawler作成時にロールを作成できるので、フォルダへのアクセス権をIAMフォルダのGlueCrawler.jsonのように追加する

# 設定ファイルuploader.jsonについて

## Directories
作業ディレクトリの相対パス
- Output 前段のプログラムの出力先
- Outobox 本プログラムの入力。Outputのファイルを移動することで共有違反を回避する目的
- Sent 送信済みファイルをZipアーカイブして保存するフォルダ

## AWS
### GlueFields
Glue Crawlerで認識できない列型をstringとして扱うならtrue
### profile
~/.aws/config に定義されたプロファイル名
### multipart_MB
マルチパートアップロードのサイズ
### BucketName
アップロード先バケット名
### ObjectKeyPrefix
バケットの先のプリフィックス名。/で終わる文字列を指定すること。
### Partiton
Glue TableのPartiton登録。TableそのものはGlue Crawlerなどで作成する想定。

- Database
Database名

- Table
Table名

- Keys
Partiton Keyのkeyとvalue（strftimeで読み替える）

### Tagging
コスト配分タグなどのタグ付け。プロジェクト単位でストレージコストを計算したい時などに利用可能。