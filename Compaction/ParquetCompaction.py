import json
import concurrent.futures
import datetime
import os
import boto3
import botocore.exceptions
import pyarrow
from pyarrow import table as pt
import pyarrow.parquet as pq


def download_file(s3_client, index: int, s3_key: str) -> pt:
    """1ファイルをローカルの /tmp にダウンロードする関数"""
    local_path = f"/tmp/{index}.parquet"
    s3_client.download_file(os.environ["bucket"], s3_key, local_path)
    tbl = pq.read_table(local_path)
    os.remove(local_path)  # ダウンロード後は即削除してtmpを空き容量ゼロに近づける
    return tbl


def get_target_s3_keys(s3_client, src: str, dt: datetime.date) -> list:
    """処理対象のS3オブジェクトキーのリストを返す関数"""
    prefix = f"{src}/year={dt.strftime('%Y')}/month={dt.strftime('%m')}/day={dt.strftime('%d')}/"

    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=os.environ["bucket"], Prefix=prefix)

    s3_keys = []
    for page in pages:
        if "Contents" in page:
            for obj in page["Contents"]:
                s3_keys.append(obj["Key"])
    return s3_keys


def add_partition(table_name: str, key: str, dt: datetime.date):
    glue_client = boto3.client("glue")
    try:
        glue_client.create_partition(
            DatabaseName=os.environ["database"],
            TableName=table_name,
            PartitionInput={
                "Values": [
                    dt.strftime("%Y"),
                    dt.strftime("%m"),
                    dt.strftime("%d"),
                ],
                "StorageDescriptor": {
                    "Location": f"s3://{os.environ['bucket']}/{key}",
                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    "NumberOfBuckets": -1,
                    "SerdeInfo": {
                        "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                    },
                },
            },
        )
    except botocore.exceptions.ClientError as e:
        # print(e)
        pass


def lambda_handler(event, context):
    os.makedirs("/tmp", exist_ok=True)
    os.system("rm -rf /tmp/*")

    # 1. 処理対象のS3キーリストを取得 (例: 2026年8月26日分)
    # ListObjectsV2 などで対象のS3キーのリストを取得する前提
    # s3_keys = ['raw/2026/08/26/file1.parquet', ...]
    dt = datetime.date(event["year"], event["month"], event["day"])
    s3_client = boto3.client("s3")
    s3_keys = get_target_s3_keys(
        s3_client, event["src"], dt
    )  # 対象ファイルのリストアップ
    # print('\n'.join(s3_keys))

    # 2. マルチスレッドでS3から一気にダウンロード（I/Oの隠蔽）
    # worker数はLambdaのvCPU数やネットワーク帯域に合わせて調整（例: 10〜20）
    max_workers = 16
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(download_file, s3_client, i, key)
            for i, key in enumerate(s3_keys)
        ]
        concurrent.futures.wait(futures)

    # 3. ローカルに落ちたファイルをPyArrowのDatasetで読み込み＆結合
    # パーティション列が混入することなく、純粋なデータだけを読み込めます
    table = pyarrow.concat_tables(
        [r.result() for r in futures]
    )  # これで各ファイルのテーブルがリストで取得できる

    # 4. 綺麗な1つのParquetファイルとしてローカルに書き出し
    output_local_path = "/tmp/compacted_output.parquet"
    pq.write_table(table, output_local_path, compression="SNAPPY")

    # 5. まとめたファイルを最終的なHive形式のS3パスへアップロード
    output_s3_dir = f"{event['dst']}/year={dt.strftime('%Y')}/month={dt.strftime('%m')}/day={dt.strftime('%d')}/"
    output_s3_key = output_s3_dir + f"{dt.strftime('%Y%m%d')}_{event['dst']}.parquet"
    s3_client.upload_file(output_local_path, os.environ["bucket"], output_s3_key)
    # print(output_s3_key)
    add_partition(event["dst"], output_s3_dir, dt)

    # 6. /tmp の一時ファイルをクリーンアップ（Lambdaの仕様上必須ではないが安全のため）
    # ...（ローカルファイルの削除処理）
    os.system("rm -rf /tmp/*")

    return {"statusCode": 200, "body": json.dumps("Normal end.")}
