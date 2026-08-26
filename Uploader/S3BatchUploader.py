# S3Uploader.py
# io IOBase.writable でファイルを削除可能かチェックする

import datetime  # 日付計算
import json  # 設定用JSONファイルの解釈
import threading  # zipアーカイブのマルチスレッド処理
import logging  # ログ出力
import shutil  # ファイル移動
import os  # ファイル削除
import sys
import glob  # ファイル一覧取得
import zipfile  # zipアーカイブ
import queue  # 送信済みファイルのキュー

# AWS関連
# miniconda3へのAWS CLIインストールコマンド conda install -c anaconda boto3
import boto3
import boto3.s3.transfer
from botocore.exceptions import ClientError
from botocore.exceptions import ParamValidationError
from botocore.exceptions import NoCredentialsError

import file_transfer  # AWSからダウンロードしたpythonコード

INI = "uploader.json"  # 同じフォルダにおいてある設定用JSONファイル

# ループ続行用の変数
g_loop = True

# サブフォルダ名の初期値
outputDir = "./output"
outboxDir = "./outbox"
sentDir = "./sent"

# ------------------------------------------------------------------------------#


# アップロード中のコールバック関数
class TransferCallback:
    """
    Handle callbacks from the transfer manager.

    The transfer manager periodically calls the __call__ method throughout
    the upload and download process so that it can take action, such as
    displaying progress to the user and collecting data about the transfer.
    """

    def __init__(self, target_size):
        self._target_size = target_size
        self._total_transferred = 0
        self._lock = threading.Lock()
        self.thread_info = {}
        self.filename = ""

    def __call__(self, bytes_transferred):
        """
        The callback method that is called by the transfer manager.

        Display progress during file transfer and collect per-thread transfer
        data. This method can be called by multiple threads, so shared instance
        data is protected by a thread lock.
        """
        thread = threading.current_thread()

        MB = 1024 * 1024
        with self._lock:
            self._total_transferred += bytes_transferred
            if thread.ident not in self.thread_info.keys():
                self.thread_info[thread.ident] = bytes_transferred
            else:
                self.thread_info[thread.ident] += bytes_transferred

            target = self._target_size * MB
            sys.stdout.write(
                f"\r{self.filename} {self._total_transferred} of {target} transferred "
                f"({(self._total_transferred / target) * 100:.2f}%)."
            )
            sys.stdout.flush()

    def reset(self, filename):
        self._total_transferred = 0
        self.filename = filename


# S3へアップロードを行うオブジェクト
# Windowsの認証情報は       %USERPROFILE%\.aws\credentials
# Mac, Linuxの認証情報は    ~/.aws/credentials
class S3Uploader:
    ext = "*.parquet"  # アップロードファイル拡張子に合わせて変更

    def __init__(self):
        global outputDir
        global outboxDir
        global sentDir

        tagging = None
        self.glue_client = None
        with open(INI, "r") as fj:  # 自動クローズ
            settings = json.load(fj)

        outputDir = settings["Directories"]["Output"]
        outboxDir = settings["Directories"]["Outbox"]
        if os.path.exists(outboxDir) == False:
            os.mkdir(outboxDir)
        sentDir = settings["Directories"]["Sent"]
        if os.path.exists(sentDir) == False:
            os.mkdir(sentDir)

        self.BucketName = settings["AWS"]["BucketName"]
        self.Prefix = settings["AWS"]["ObjectKeyPrefix"]
        self.dateDirDef = (
            "/".join(
                [
                    f"{k['key']}={k['value']}"
                    for k in settings["AWS"]["Partition"]["Keys"]
                ]
            )
            + "/"
        )
        if "Tagging" in settings["AWS"].keys():
            tagging = "&".join(
                [f"{k}={v}" for k, v in settings["AWS"]["Tagging"].items()]
            )

        self.partition = settings["AWS"]["Partition"]
        if 0 < len(self.partition["Database"]):
            self.glue_client = boto3.client("glue")

        # profile指定のためsessionを使う
        self.session = boto3.Session(profile_name=settings["AWS"]["profile"])
        self.s3_client = self.session.client("s3")
        self.extra_args = {"Tagging": tagging} if tagging else None
        multipart_MB = int(settings["AWS"]["multipart_MB"])
        self.config = boto3.s3.transfer.TransferConfig(
            multipart_threshold=multipart_MB, multipart_chunksize=multipart_MB
        )
        self.callback = TransferCallback(multipart_MB)

    def Upload(self, filename):
        # print( '\n' + self.Prefix + '/' + self.dateDir + '/' + filename )

        # ファイル名からAWSでの保存先を指定（変換後のファイルなので、ファイルのタイムスタンプは利用不可）
        filenameDate = datetime.datetime(
            int(filename[0:4]),
            int(filename[4:6]),
            int(filename[6:8]),
            int(filename[9:11]),
            int(filename[11:13]),
            int(filename[13:15]),
        )

        # s3のパスは、要件に合わせて適宜生成のこと。
        # 1ファイルを複数のテーブルに分割する場合、
        # プロジェクト名/テーブル名/インデックスキー（key=01など）/インデックスキー/.../ファイル名 と命名する。
        # テーブル名を短くし過ぎるとAthenaで複数のプロジェクトのテーブルを表示したときにわからなくなるので、
        # テーブル名は「何のプロジェクトのどのテーブル」が分かるように命名すること。
        s3Path = self.Prefix + filenameDate.strftime(self.dateDirDef) + filename
        # print(s3Path)

        # アップロード実行
        self.callback.reset(filename)
        self.s3_client.upload_file(
            outboxDir + "/" + filename,
            self.BucketName,
            s3Path,
            self.extra_args,
            self.callback,
            self.config,
        )

        # パーティション追加
        if not self.glue_client:
            return  # パーティション追加対象のテーブルが指定されていない場合

        import botocore.exceptions

        try:
            # 既存のパーティションに追加を試みると例外をスローするので、最後にパーティション追加を実施
            self.glue_client.create_partition(
                DatabaseName=self.partition["Database"],
                TableName=self.partition["Table"],
                PartitionInput={
                    "Values": [
                        filenameDate.strftime(i["value"])
                        for i in self.partition["Keys"]
                    ],
                    "StorageDescriptor": {
                        "Location": f"s3://{self.BucketName}/{s3Path}",
                        "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                        "NumberOfBuckets": -1,
                        "SerdeInfo": {
                            "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                        },
                    },
                },
            )
        except botocore.exceptions.ClientError as e:
            # 1分単位でpartitionを作成する想定で、「時間や日ごとなら衝突しても仕方ない」方式
            # 初期にEntityNotFoundExceptionが出るのは仕方ないので、AWSコンソールで動作確認する前提
            errcode = e.response["Error"]["Code"]
            if errcode in ["AlreadyExistsException", "EntityNotFoundException"]:
                pass  # 既にあるなら何もしない。テーブルが無くてもデータは貯める
            else:
                raise RuntimeError(f"S3Uploader.UploadBuffer() {type(e)} {e}") from e


# ------------------------------------------------------------------------------#


# ファイルのアーカイブ処理
class LocalArchiver:
    def __init__(self):
        self.ev = threading.Event()
        self.ev.clear()
        self.sources = queue.SimpleQueue()

        # アーカイブ目的にGZIPを使うと、Windows環境では展開が大変なので、普通のzipを使う。
        # zipfileライブラリはファイルサイズの縮小を行わない（動作は軽いが容量メリットなし）
        # 1分で1ファイルなら、1日1440ファイル、1週間10080ファイル。
        # 1時間で1ファイルなら、1日24ファイル、1週間168ファイル。
        self.zipFileName = (
            datetime.datetime.now().strftime("%Y%m%d_%H%H%S") + "_sent.zip"
        )  # 1バッチ1ファイル
        self.filehandle = zipfile.ZipFile(sentDir + "/" + self.zipFileName, "a")

    def __del__(self):
        if self.filehandle != None:
            self.filehandle.close()

    # 外部からの保管指令
    def Archive(self, outboxFilename):  # 外部から保管指令
        global logging
        self.sources.put(outboxFilename)
        try:
            # zipファイル内はフォルダを作らない
            self.filehandle.write(outboxDir + "/" + outboxFilename, outboxFilename)
            os.remove(outboxDir + "/" + outboxFilename)
        except BaseException as e:
            logging.error(
                str(datetime.datetime.now()) + "\t LocalArchiver.WaitEvent()" + str(e)
            )


# ------------------------------------------------------------------------------#


# オープンされていないファイルをoutputからoutboxへ移動
def OutputToOutbox():
    global uploader
    files = glob.glob(outputDir + "/" + uploader.__class__.ext)
    for f in files:
        try:
            shutil.move(f, outboxDir + "/")
        except PermissionError as e:
            # 例外が発生してもファイルコピーは行われるので、ファイルを削除
            print(f + " is being written now.")
            os.remove(f[f.rfind("\\") + 1 :])


# ------------------------------------------------------------------------------#


# sentフォルダのファイル容量が一定値を超えたら、超過分を削除
def DeleteOldFiles():
    global logging
    # print('DeleteOldFiles()')

    try:
        files = os.listdir(sentDir)

        # 最終更新時刻の降順ソート
        sorted(files, key=lambda f: os.stat(sentDir + "/" + f).st_mtime, reverse=True)
        total_size = 0
        threshold = 1024 * 1024 * 1024  # byte単位

        try:
            for i in files:
                filename = sentDir + "/" + i
                # print(filename)
                total_size += os.stat(filename).st_size  # 合計サイズを計算
                # print(total_size)

                if threshold < total_size:
                    os.remove(filename)
                    print(filename + " is deleted.")
        except PermissionError:
            pass
    except BaseException as e:
        logging.error(
            str(datetime.datetime.now()) + "\t LocalArchiver.WaitEvent()" + str(e)
        )
        g_loop = False


# ------------------------------------------------------------------------------#

# main routine
try:
    logging.basicConfig(filename="s3BatchUploader.log", level=logging.INFO)

    DeleteOldFiles()  # archiverのデストラクタでzipファイルを閉じるので先に掃除しておく

    uploader = S3Uploader()
    archiver = LocalArchiver()
    OutputToOutbox()

    # S3へ送信
    files = os.listdir(outboxDir)
    for i in files:
        uploader.Upload(i)  # S3へ送信
        archiver.Archive(i)  # 送信済みファイルをアーカイブ

    print("normal end")
except BaseException as e:
    logging.error(str(datetime.datetime.now()) + "\t" + str(e))
    print("abnormal end")
