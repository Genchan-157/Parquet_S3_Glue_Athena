# 2026/08/01 UDP_Receive.py

import datetime  # 日付処理
import ctypes
import array
import json
import logging
import threading
import multiprocessing
import multiprocessing.connection
from multiprocessing.sharedctypes import RawArray

from math import nan
import pyarrow

INI = "settings.json"  # 同じフォルダにおいてある設定用JSONファイル

# 参照速度を優先して、グローバル定数を宣言
_EPOCH = datetime.datetime(1970, 1, 1)  # naive datetime（UTC基準）
_US = datetime.timedelta(microseconds=1)
_bitflags = array.array("B", [0x1 << i for i in range(0, 8)])  # uint8

# ------------------------------------------------------------------------------#


# bool配列型の基本クラス 共通処理
class BoolField_Base:
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_uint16)]

    fieldbits = 16
    fieldbytes = 2
    filter = array.array("I", [0x1 << i for i in range(0, 32)])  # uint32

    def __init__(self, cols: list, length: int):
        self.length = length
        self.colnameflag = 0x0  # 有効列フラグ
        self.elements = list()

        ctr0 = 0
        while True:
            if cols[ctr0] != "":
                self.colnameflag |= self.filter[ctr0]

                # 共有メモリ配列の初期化 bool8つで1byte単位
                n = self.length >> 3  # 8で割った商
                self.elements.append(
                    RawArray("B", (n if (self.length & 0x7) == 0 else n + 1))
                )
            ctr0 += 1
            if self.__class__.fieldbits <= ctr0:
                break

    def from_bytes(self, index: array.array, row: int, data):
        ctr0 = 0  # 参照列
        ctr1 = 0  # 書込列

        dat = self.__class__.MyObj.from_buffer(data, index[0]).value
        while ctr0 < self.__class__.fieldbits:
            if self.filter[ctr0] & self.colnameflag:
                if self.filter[ctr0] & dat:
                    self.elements[ctr1][row >> 3] |= _bitflags[row & 0x7]
                else:
                    self.elements[ctr1][row >> 3] &= ~_bitflags[row & 0x7]
                ctr1 += 1  # 次の有効列に書込
            ctr0 += 1  # 次のビットを参照
        index[0] += self.__class__.fieldbytes

    # 参照データ1つ（16bit, 32bit）に対して複数配列を出力
    def get_array(self) -> list:
        return [
            pyarrow.BooleanArray.from_buffers(
                pyarrow.bool_(), self.length, [None, pyarrow.py_buffer(i)]
            )
            for n, i in enumerate(self.elements)
        ]


class Bool16Field(BoolField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_uint16)]

    fieldbytes = ctypes.sizeof(MyObj)
    fieldbits = fieldbytes << 3

    def __init__(self, cols: list, length: int):
        if len(cols) != self.__class__.fieldbits:
            raise RuntimeError("Bool16Field.__init__() column name length is not 16")
        super().__init__(cols, length)


class Bool32Field(BoolField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_uint32)]

    fieldbytes = ctypes.sizeof(MyObj)
    fieldbits = fieldbytes << 3

    def __init__(self, cols: list, length: int):
        if len(cols) != self.__class__.fieldbits:
            raise RuntimeError("Bool32Field.__init__() column name length is not 32")
        super().__init__(cols, length)


# 整数フィールド（基底クラス）
class IntField_Base:
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_int16)]

    MyTypeCode = "h"  # 16bit int
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        self.length = int(length)
        self.min = None if minVal is None else int(minVal)
        self.max = None if maxVal is None else int(maxVal)

        # boolバッファの初期化 bool8つで1byte単位
        n = self.length >> 3
        # 0x0や0xFFで初期化すると正常に動作しなかったため0x1で初期化
        self.not_nan_flag = RawArray(
            "B", [0x1] * (n if (self.length & 0x7) == 0 else n + 1)
        )
        self.elements = RawArray(self.__class__.MyTypeCode, [0] * self.length)

    def from_bytes(self, index: array.array, row: int, data):
        value = self.__class__.MyObj.from_buffer(data, index[0]).value
        self.elements[row] = value

        if (self.min is not None and value < self.min) or (
            self.max is not None and self.max < value
        ):
            self.not_nan_flag[row >> 3] &= ~_bitflags[row & 0x7]
        else:
            self.not_nan_flag[row >> 3] |= _bitflags[row & 0x7]

        index[0] += self.__class__.fieldbytes


# int16
class Int16Field(IntField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_int16)]

    MyTypeCode = "h"  # 16bit int
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        super().__init__(length, minVal, maxVal)

    def get_array(self) -> list:
        return [
            pyarrow.Int16Array.from_buffers(
                pyarrow.int16(),
                self.length,
                [
                    pyarrow.py_buffer(self.not_nan_flag),
                    pyarrow.py_buffer(self.elements),
                ],
            )
        ]


# int32
class Int32Field(IntField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_int32)]

    MyTypeCode = "i"  # 32bit int
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        super().__init__(length, minVal, maxVal)

    def get_array(self) -> list:
        return [
            pyarrow.Int32Array.from_buffers(
                pyarrow.int32(),
                self.length,
                [
                    pyarrow.py_buffer(self.not_nan_flag),
                    pyarrow.py_buffer(self.elements),
                ],
            )
        ]


# int64
class Int64Field(IntField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_int64)]

    MyTypeCode = "q"  # 64bit int
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        super().__init__(length, minVal, maxVal)

    def get_array(self) -> list:
        return [
            pyarrow.Int64Array.from_buffers(
                pyarrow.int64(),
                self.length,
                [
                    pyarrow.py_buffer(self.not_nan_flag),
                    pyarrow.py_buffer(self.elements),
                ],
            )
        ]


# 浮動小数点フィールド（基底クラス）
class FloatingPointField_Base:
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_float)]

    MyTypeCode = "f"
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        self.elements = RawArray(self.__class__.MyTypeCode, [0] * int(length))
        self.min = None if minVal is None else float(minVal)
        self.max = None if maxVal is None else float(maxVal)

    def from_bytes(self, index: array.array, row: int, data):
        # Cのバイト列からオブジェクトに読み込み
        value = self.__class__.MyObj.from_buffer(data, index[0]).value
        self.elements[row] = (
            nan
            if (self.min is not None and value < self.min)
            or (self.max is not None and self.max < value)
            else value
        )
        index[0] += self.__class__.fieldbytes


# float32
class Float32Field(FloatingPointField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_float)]

    MyTypeCode = "f"
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        super().__init__(length, minVal, maxVal)

    def get_array(self) -> list:
        return [
            pyarrow.FloatArray.from_buffers(
                pyarrow.float32(),
                len(self.elements),
                [None, pyarrow.py_buffer(self.elements)],
            )
        ]


# float64
class Float64Field(FloatingPointField_Base):
    class MyObj(ctypes.LittleEndianStructure):
        _fields_ = [("value", ctypes.c_double)]

    MyTypeCode = "d"
    fieldbytes = ctypes.sizeof(MyObj)

    def __init__(self, length: int, minVal, maxVal):
        super().__init__(length, minVal, maxVal)

    def get_array(self) -> list:
        return [
            pyarrow.DoubleArray.from_buffers(
                pyarrow.float64(),
                len(self.elements),
                [None, pyarrow.py_buffer(self.elements)],
            )
        ]


# 文字列型の抽象基本クラス
class StringField:
    encoding = "utf_8"
    endchar = "\0"

    def __init__(self, bytelength: int, arraylength: int):
        self.__class__.endchar = "\0".encode(
            self.__class__.encoding
        )  # utf_16等終端文字がマルチバイトの場合の対策

        # arraylengthは呼び出し側で(1ファイル当たりの最大行数+1)*2を指定している
        # 1ファイル60行とした場合、前半の0-59がindex[0:60]、後半の60-119が[61:121]で最低122行が必要。
        self.arraylength = int(arraylength)
        self.in_bytelength = int(bytelength)

        # ASCIIエンコーディングはそのままUTF-8コードになるため入出力で同じ長さ
        # それ以外は半角カナ（1byte->3bytes）を考慮して3倍
        if self.__class__.encoding == "ascii":
            self.out_bytelength = self.in_bytelength
        elif self.__class__.encoding[0:6] == "utf_16":
            self.out_bytelength = self.in_bytelength + self.in_bytelength // 2  # 1.5倍
        else:
            self.out_bytelength = self.in_bytelength * 3  # shift_jis等

        # 中身と見出しの配列
        bufflen = self.out_bytelength * self.arraylength  # 前半・後半合わせたバッファ長
        self.elements = RawArray("B", bufflen)  # 中身
        self.indexes = RawArray("i", self.arraylength + 1)  # 最初の0があるので1追加

        # バッファインデックス（前半・後半）の初期化
        self.indexes[0] = 0
        self.indexes[self.arraylength // 2] = bufflen // 2

    def from_bytes(self, index: array.array, row: int, data):
        start = self.indexes[row]  # 書き込み開始位置を取得

        # データの読み込み：NULLで埋めている部分は切り捨てる
        n = (
            data.find(self.__class__.endchar, index[0], index[0] + self.in_bytelength)
            - index[0]
        )  # NULLまでのバイト数
        if n < 0:
            n = self.in_bytelength

        b = str(
            (ctypes.c_ubyte * n).from_buffer(data, index[0]), self.__class__.encoding
        ).encode()
        lb = len(b)  # UTF-8のバイト数

        # データの書き込み
        self.elements[start : start + lb] = b  # 配列に書き込み
        self.indexes[row + 1] = start + lb  # 次のstart位置

        index[0] += self.in_bytelength  #  次の読み込み位置

    def get_array(self) -> list:
        return [
            pyarrow.StringArray.from_buffers(
                self.arraylength,
                pyarrow.py_buffer(self.indexes),
                pyarrow.py_buffer(self.elements),
            )
        ]


class ASCII_Field(StringField):
    encoding = "ascii"

    def __init__(self, bytelength: int, arraylength: int):
        super().__init__(bytelength, arraylength)


class ShiftJIS_Field(StringField):
    encoding = "cp932"  # cp932は丸数字対応

    def __init__(self, bytelength: int, arraylength: int):
        super().__init__(bytelength, arraylength)


class UTF16LE_Field(StringField):
    encoding = "utf_16_le"

    def __init__(self, bytelength: int, arraylength: int):
        super().__init__(bytelength, arraylength)


# 時刻フィールドの基本クラス
class TimeFieldBase:
    def __init__(self, length: int):
        self.elements = RawArray("q", [0] * length)  # 64bit int

    def get_array(self) -> list:
        return [
            pyarrow.TimestampArray.from_buffers(
                pyarrow.timestamp("us"),
                len(self.elements),
                [None, pyarrow.py_buffer(self.elements)],
            )
        ]


# Python実行環境のUTC時刻
class UtcTimeField(TimeFieldBase):
    fieldbytes = 0

    def __init__(self, length: int):
        super().__init__(length)

    def from_bytes(self, index: array.array, row: int, data):
        dt = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self.elements[row] = int((dt - _EPOCH) / _US)


# Python実行環境のローカル時刻
class LocalTimeField(TimeFieldBase):
    tz = datetime.timezone.utc  # プロセスの初期化時に、ここも初期化してもらっている
    fieldbytes = 0

    def __init__(self, length: int):
        super().__init__(length)

    def from_bytes(self, index: array.array, row: int, data):
        dt = (
            datetime.datetime.now(self.__class__.tz)
            .astimezone(datetime.timezone.utc)
            .replace(tzinfo=None)
        )
        self.elements[row] = int((dt - _EPOCH) / _US)


# MELSEC-QのDATERD命令からの読み取り
class MelsecDateTimeField(LocalTimeField):
    # DATERD命令の戻り値
    class DATERD(ctypes.LittleEndianStructure):
        _fields_ = [
            ("year", ctypes.c_int16),
            ("month", ctypes.c_int16),
            ("day", ctypes.c_int16),
            ("hour", ctypes.c_int16),
            ("min", ctypes.c_int16),
            ("sec", ctypes.c_int16),
            ("dayOfWeek", ctypes.c_int16),
        ]

    fieldbytes = ctypes.sizeof(DATERD)

    def __init__(self, length: int):
        super().__init__(length)

    def from_bytes(self, index: array.array, row: int, data):
        t = self.__class__.DATERD.from_buffer(data, index[0])
        dt = (
            datetime.datetime(
                t.year, t.month, t.day, t.hour, t.min, t.sec, 0, self.__class__.tz
            )
            .astimezone(datetime.timezone.utc)
            .replace(tzinfo=None)
        )
        self.elements[row] = int((dt - _EPOCH) / _US)
        index[0] += self.__class__.fieldbytes


# MELSEC-QのS.DATERD命令からの読み取り（ms付き）
class MelsecDateTimeMsField(LocalTimeField):
    # S.DATERD命令の戻り値
    class SDATERD(ctypes.LittleEndianStructure):
        _fields_ = [
            ("year", ctypes.c_int16),
            ("month", ctypes.c_int16),
            ("day", ctypes.c_int16),
            ("hour", ctypes.c_int16),
            ("min", ctypes.c_int16),
            ("sec", ctypes.c_int16),
            ("dayOfWeek", ctypes.c_int16),
            ("ms", ctypes.c_int16),
        ]

    fieldbytes = ctypes.sizeof(SDATERD)

    def __init__(self, length: int):
        super().__init__(length)

    def from_bytes(self, index: array.array, row: int, data):
        t = self.__class__.SDATERD.from_buffer(data, index[0])
        dt = (
            datetime.datetime(
                t.year,
                t.month,
                t.day,
                t.hour,
                t.min,
                t.sec,
                t.ms * 1000,
                self.__class__.tz,
            )
            .astimezone(datetime.timezone.utc)
            .replace(tzinfo=None)
        )
        self.elements[row] = int((dt - _EPOCH) / _US)
        index[0] += self.__class__.fieldbytes


# ------------------------------------------------------------------------------#


# UDPオブジェクト
class UDP_Listener:
    def __init__(self, index: int, ip: str, receiver: dict):
        import socket

        self.index = index
        self.ip = ip

        # ファイル名の時間帯
        self.fnFileTime = (
            (lambda: datetime.datetime.now(datetime.timezone.utc))
            if (receiver["output"]["fileTimezone"] == "UTC")
            else (lambda: datetime.datetime.now())
        )

        # ファイル作成間隔
        self.fileInterval = datetime.timedelta(
            minutes=int(receiver["output"]["intervalMinutes"])
        )
        self.fields = list()

        # json読み込み
        self.port = int(receiver["UDP_Port"])
        self.maxRowCountPerFile = int(receiver["output"]["MaxRowCountPerFile"])

        # StringFieldの初期インデックス用に1行余分に確保する
        array_length = 2 * (self.maxRowCountPerFile + 1)

        # 各フィールド初期化
        self.dgramlen = 0
        v = receiver["fields"]
        for o in v:
            if o["type"] == "bool16":
                f = Bool16Field(o["name"], array_length)
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "bool32":
                f = Bool32Field(o["name"], array_length)
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "int16":
                f = Int16Field(
                    array_length,
                    o["min"] if "min" in o else None,
                    o["max"] if "min" in o else None,
                )
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "int32":
                f = Int32Field(
                    array_length,
                    o["min"] if "min" in o else None,
                    o["max"] if "min" in o else None,
                )
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "int64":
                f = Int64Field(
                    array_length,
                    o["min"] if "min" in o else None,
                    o["max"] if "min" in o else None,
                )
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "float32":
                f = Float32Field(
                    array_length,
                    o["min"] if "min" in o else None,
                    o["max"] if "min" in o else None,
                )
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "float64":
                f = Float64Field(
                    array_length,
                    o["min"] if "min" in o else None,
                    o["max"] if "min" in o else None,
                )
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "utcTime":
                f = UtcTimeField(array_length)
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "localTime":
                f = LocalTimeField(array_length)
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "datetime":
                f = MelsecDateTimeField(array_length)
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "datetime_ms":
                f = MelsecDateTimeMsField(array_length)
                self.fields.append(f)
                self.dgramlen += f.fieldbytes
            elif o["type"] == "ascii":
                f = ASCII_Field(o["bytes"], array_length)
                self.fields.append(f)
                self.dgramlen += f.in_bytelength
            elif o["type"] == "shift_jis":
                f = ShiftJIS_Field(o["bytes"], array_length)
                self.fields.append(f)
                self.dgramlen += f.in_bytelength
            elif o["type"] == "utf_16_le":
                f = UTF16LE_Field(o["bytes"], array_length)
                self.fields.append(f)
                self.dgramlen += f.in_bytelength
            else:
                raise ValueError(
                    "schema.json field type must be [utcTime, localTime, datetime, bool16, bool32, int16, int32, float32, float64, ascii, shift_jis, utf16_le]"
                )

        # self.field_repeatを繰り返し回数に合わせて増やす
        self.field_repeat = (
            int(receiver["field_repeat"]) if "field_repeat" in receiver else 1
        )
        self.dgramlen *= self.field_repeat
        # print(f"field repeat = {self.field_repeat}")

        # 電文待ち受けスレッド 受信待機中は何も反応しないのでdaemon=True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP/IP
        self.sock.bind((self.ip, self.port))  # 失敗時はスレッド起動前に例外
        print(f"UDP Listening {self.ip}:{self.port}")
        self.connRecv, connSend = multiprocessing.Pipe(False)
        self.thread = threading.Thread(
            target=self.ThreadMain,
            name=f"{self.ip}:{self.port}",
            args=(connSend, self.field_repeat),
            daemon=True,
        )
        self.thread.start()

    # 電文待機
    def ThreadMain(
        self, connSend: multiprocessing.connection.Connection, field_repeat: int
    ):
        bShift = False
        hwm = array.array("H", [0, 0])
        futuretime = self.fnFileTime() + self.fileInterval
        filetimestamp = datetime.datetime.now()  # Pyloance警告回避のため初期化
        data = bytearray(self.dgramlen)
        index = array.array("H", [0])
        col = 0  # 列番号
        row = 0  # 繰り返し番号
        fldlen = len(self.fields)
        bufferlen = 2 * (self.maxRowCountPerFile + 1)

        try:
            while True:
                length, addr = self.sock.recvfrom_into(data, self.dgramlen)
                ft = self.fnFileTime()

                # 出力先の入れ替え
                bSwitch = futuretime < ft
                if bSwitch:
                    filetimestamp = futuretime  # 出力用に保存
                    futuretime = ft + self.fileInterval
                    bShift = not bShift
                    hwm[1 if bShift else 0] = 0

                # 書き込み先の計算
                row = bufferlen  # 書き込み無効（オーバーフロー）で初期化
                if bShift:
                    if hwm[1] < self.maxRowCountPerFile:
                        row = hwm[1] + (self.maxRowCountPerFile + 1)
                else:
                    if hwm[0] < self.maxRowCountPerFile:
                        row = hwm[0]

                # 受信1回分の書き込み
                if row < bufferlen:
                    index[0] = 0
                    rep = 0
                    while rep < field_repeat:
                        col = 0
                        while col < fldlen:
                            # メモリが断片化抑制を狙って、ミュータブルな配列渡し
                            self.fields[col].from_bytes(index, row, data)
                            col += 1
                        # Parquetの1行分出力終了
                        hwm[1 if bShift else 0] += 1
                        rep += 1  # 繰り返しカウンタ
                        row += 1  # 書き込み先の行

                # 書き込んだ後で出力処理起動（ジッター軽減）
                if bSwitch:
                    b = not bShift
                    h = hwm[1 if b else 0]
                    if 0 < h:  # ファイルに書き込みがあった場合
                        connSend.send((b, h, filetimestamp))
        except BaseException as e:
            # エラーが起きたらログに書いて終了
            logging.error(
                f"{datetime.datetime.now()} UDP_Listener.ThreadMain() listener={self.index} row={row} col={col} index={index[0]} {type(e)} {e}"
            )
            self.sock.close()
            print(f"Port Closed {self.ip}:{self.port}")
            connSend.send(None)  # 異常終了を通知


# ------------------------------------------------------------------------------#


class Uploader:

    def __init__(
        self,
        proxy,  # dict / None
        index: int,
        receiver: dict,
        fields: list,
        connRecv: multiprocessing.connection.Connection,
    ):
        import boto3
        import botocore
        import botocore.config
        import pyarrow.fs

        self.glue_client = None
        self.index = index
        self.proxy = proxy
        s3fs_config = receiver["AWS"]["S3FileSystem"]
        self.s3fs = pyarrow.fs.S3FileSystem(
            background_writes=False,
            proxy_options=proxy,
            access_key=s3fs_config["access_key"] if "region" in s3fs_config else None,
            secret_key=s3fs_config["secret_key"] if "region" in s3fs_config else None,
            region=(
                s3fs_config["region"] if "region" in s3fs_config else "ap-northeast-1"
            ),
            request_timeout=(
                s3fs_config["request_timeout"]
                if "request_timeout" in s3fs_config
                else None
            ),
            connect_timeout=(
                s3fs_config["connect_timeout"]
                if "connect_timeout" in s3fs_config
                else None
            ),
            role_arn=s3fs_config["role_arn"] if "role_arn" in s3fs_config else None,
            session_name=(
                s3fs_config["session_name"] if "session_name" in s3fs_config else None
            ),
            external_id=(
                s3fs_config["external_id"] if "external_id" in s3fs_config else None
            ),
            load_frequency=(
                s3fs_config["load_frequency"]
                if "load_frequency" in s3fs_config
                else 900
            ),
            tls_ca_file_path=(
                s3fs_config["tls_ca_file_path"]
                if "external_id" in s3fs_config
                else None
            ),
        )
        self.name = receiver["name"]
        self.maxRowCountPerFile = int(receiver["output"]["MaxRowCountPerFile"])
        self.BucketName = receiver["AWS"]["BucketName"]

        self.partition = receiver["AWS"]["Partition"]
        if 0 < len(self.partition["Database"]):
            self.glue_client = boto3.client("glue")

        self.objectkey = [
            receiver["AWS"]["ObjectKeyPrefix"],
            "",
            "",
            f'{receiver["output"]["suffix"]}.parquet',
        ]
        if "Tagging" in receiver["AWS"].keys():
            self.tagging = json.loads(
                '{"TagSet":['
                + ",".join(
                    [
                        f'{{"Key":"{k}","Value":"{v}"}}'
                        for k, v in receiver["AWS"]["Tagging"].items()
                    ]
                )
                + "]}"
            )

        self.client = boto3.client(
            "s3",
            config=botocore.config.Config(
                proxies={
                    "https": (f'{proxy["host"]}:{proxy["port"]}' if self.proxy else "")
                }
            ),
        )

        # 出力列の登録
        colnames = list()
        v = receiver["fields"]
        for o in v:
            if o["type"] == "bool16" or o["type"] == "bool32":
                colnames.extend([i for i in o["name"] if i != ""])
            else:
                colnames.append(o["name"])

        # RawArray -> Buffer -> Array -> Tableと作成
        ctr = 0
        fldlen = len(fields)
        arrays = list()
        while ctr < fldlen:
            arrays.extend(fields[ctr].get_array())
            ctr += 1
        self.table = pyarrow.Table.from_arrays(arrays, colnames)

        threading.Thread(
            target=self.ThreadMain,
            name=f"UDP_Receive.Uploaders.{self.name}",
            args=(connRecv,),
            daemon=False,  # Falseにしないとサブプロセスが終了する
        ).start()

    def ThreadMain(self, connRecv: multiprocessing.connection.Connection):
        while True:
            msg = connRecv.recv()
            if msg is None:
                return  # 終了指令を受信
            self.UploadBuffer(msg[0], msg[1], msg[2])

    def UploadBuffer(self, bShift: bool, hwm: int, filedate: datetime.datetime):
        import sys
        import pyarrow.parquet
        import botocore.exceptions  # AWS関連

        # ファイルアップロードtry　pyarrow.fs.S3FileSystemはOS Errorを投げるのでboto3とは例外処理を分ける
        try:
            # s3のパスは、要件に合わせて適宜生成のこと。
            # 1ファイルを複数のテーブルに分割する場合、
            # プロジェクト名/テーブル名/インデックスキー（key=01など）/インデックスキー/.../ファイル名 と命名する。
            # テーブル名を短くし過ぎるとAthenaで複数のプロジェクトのテーブルを表示したときにわからなくなるので、
            # テーブル名は「何のプロジェクトのどのテーブル」が分かるように命名すること。

            # AWS S3へ書き込み、proxy_optionsはネットワーク環境に応じて設定のこと
            self.objectkey[1] = (
                "/".join(
                    [
                        f"{i['key']}={filedate.strftime(i['value'])}"
                        for i in self.partition["Keys"]
                    ]
                )
                + "/"
            )
            self.objectkey[2] = filedate.strftime("%Y%m%d_%H%M%S_")
            objectkey = "".join(self.objectkey)

            # StringFieldのインデックスが行数＋1の配列長を必要とするので、1ずらしている。
            pyarrow.parquet.write_table(
                self.table.slice((self.maxRowCountPerFile + 1) if bShift else 0, hwm),
                f"{self.BucketName}/{objectkey}",
                filesystem=self.s3fs,
            )

        except OSError as e:  # pyarrow.fielesystem.S3FileSystemの例外
            se = str(e)
            if (
                0 <= se.find("curlCode: 5,")
                or 0 <= se.find("curlCode: 6,")
                or 0 <= se.find("curlCode: 7,")
                or 0 <= se.find("curlCode: 28,")
            ):
                # コネクタ抜けやWi-Fi切断（復帰を待つ）
                sys.stdout.write(
                    f"{datetime.datetime.now()} Check Ethernet connection. {type(e)} {e} "
                    + "\n"
                )
                sys.stdout.flush()
                return
            elif 0 <= se.find("curlCode: 35,"):
                # Proxy未設定エラー：スレッド終了
                raise RuntimeError(
                    f"Set proxy settings in settings.json!  Uploader[{self.name}].UploadBuffer() index={self.index} {type(e)} {e}"
                ) from e
            else:
                raise RuntimeError(
                    f"Uploader[{self.name}].UploadBuffer() index={self.index} {type(e)} {e}"
                ) from e
        except BaseException as e:
            raise RuntimeError(
                f"Uploader[{self.name}].UploadBuffer() index={self.index} {type(e)} {e}"
            ) from e

        # タグとパーティションの追加はboto3で実施するので別のtryブロック
        try:
            # タグの追加：s3clientはスレッドセーフなのでロックしない
            if self.tagging:
                self.client.put_object_tagging(
                    Bucket=self.BucketName,
                    Key=objectkey,
                    Tagging=self.tagging,
                )

            # パーティション追加
            if not self.glue_client:
                return  # パーティション追加対象のテーブルが指定されていない場合

            # 既存のパーティションに追加を試みると例外をスローするので、最後にパーティション追加を実施
            self.glue_client.create_partition(
                DatabaseName=self.partition["Database"],
                TableName=self.partition["Table"],
                PartitionInput={
                    "Values": [
                        filedate.strftime(i["value"]) for i in self.partition["Keys"]
                    ],
                    "StorageDescriptor": {
                        "Location": f"s3://{self.BucketName}/{self.objectkey[0]}{self.objectkey[1]}",
                        "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                        "NumberOfBuckets": -1,
                        "SerdeInfo": {
                            "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                        },
                    },
                },
            )

        # パラメータエラーなどの復帰見込みがないエラーは例外を再スロー
        except (
            botocore.exceptions.EndpointConnectionError,
            botocore.exceptions.ConnectTimeoutError,
            botocore.exceptions.ReadTimeoutError,
        ):
            pass  # 回線切断なら復帰を待つ
        except (
            botocore.exceptions.SSLError,
            botocore.exceptions.HTTPClientError,
            botocore.exceptions.ProxyConnectionError,
        ) as e:
            raise RuntimeError(
                f"Check proxy settings in settings.json! Uploader[{self.name}].UploadBuffer() index={self.index} {type(e)} {e}"
            ) from e
        except botocore.exceptions.ClientError as e:
            # 1分単位でpartitionを作成する想定で、「時間や日ごとなら衝突しても仕方ない」方式
            # 初期にEntityNotFoundExceptionが出るのは仕方ないので、AWSコンソールで動作確認する前提
            errcode = e.response["Error"]["Code"]
            if errcode in ["AlreadyExistsException", "EntityNotFoundException"]:
                pass  # 既にあるなら何もしない。テーブルが無くてもデータは貯める
            else:
                raise RuntimeError(
                    f"Uploader[{self.name}].UploadBuffer() index={self.index} {type(e)} {e}"
                ) from e
        except BaseException as e:
            raise RuntimeError(
                f"Uploader[{self.name}].UploadBuffer() index={self.index} {type(e)} {e}"
            ) from e


# ------------------------------------------------------------------------------#


# Uploaderの生成
class Writer:
    def __init__(
        self,
        settings: dict,
        fieldslist: list,
        connRecvs: list,  # [multiprocessing.connection.Connection]
    ):
        multiprocessing.Process(
            target=self.ProcessMain,
            name="UDP_Receive_Writer",
            args=(settings, fieldslist, connRecvs),
            daemon=True,
        ).start()

    def ProcessMain(
        self,
        settings: dict,
        fieldslist: list,
        connRecvs: list,  # [multiprocessing.connection.Connection]
    ):
        # サブプロセスのログ
        logging.basicConfig(
            filename="./log/UDP_Receive_Writer.log", level=logging.WARNING
        )

        try:
            # オブジェクトを生成してプロセスのメインスレッドは終了
            proxy = None
            if "HttpProxy" in settings.keys():
                px = settings["HttpProxy"]
                if 0 < len(px["host"]):
                    proxy = {
                        "scheme": "http",
                        "host": px["host"],
                        "port": int(px["port"]),
                        "username": px["username"] if 0 < len(px["username"]) else None,
                        "password": px["password"] if 0 < len(px["password"]) else None,
                    }
            for n, (receiver, fields, connRecv) in enumerate(
                zip(settings["Receivers"], fieldslist, connRecvs)
            ):
                Uploader(proxy, n, receiver, fields, connRecv)
        except BaseException as e:
            logging.error(f"{datetime.datetime.now()} {type(e)} {e}")


# ------------------------------------------------------------------------------#
# Stop key Input
class Stopper:
    def __init__(self):
        threading.Thread(
            target=self.ThreadMain, name="UDP_Receive.Stopper", daemon=False
        ).start()

    def ThreadMain(self):
        global g_ev
        try:
            while True:
                keyin = input("Input 'x' and 'Enter' if you want to stop listening.\n")
                if keyin == "x":
                    g_ev.set()
                    break
        except EOFError:
            pass  # コンソールなし -> 何もせずにスレッド終了


# ------------------------------------------------------------------------------#


# 設定ファイルに依存している初期設定
def Factory():
    import os

    try:
        with open(INI, "r") as fj:
            settings = json.load(fj)

        # 環境変数の設定
        if "FileComment" in settings.keys():
            print(f"setting.json : {settings['FileComment']}")
        proxy_str = (
            f"http://{settings['HttpProxy']['host']}:{settings['HttpProxy']['port']}"
            if 0 < len(settings["HttpProxy"]["host"])
            else ""
        )
        print(
            f"HTTP/HTTPS Proxy: {proxy_str}"
            if len(proxy_str) > 0
            else "HTTP/HTTPS Proxy:None"
        )
        os.environ["HTTP_PROXY"] = proxy_str
        os.environ["HTTPS_PROXY"] = proxy_str

        # ローカルタイムゾーンは受信側基準
        localTZ = settings["localTimezone"]
        hm = localTZ.split(":")
        LocalTimeField.tz = datetime.timezone(
            datetime.timedelta(hours=int(hm[0]), minutes=int(hm[1]))
        )

        # UDP_ListenerスレッドとWriterプロセスの初期化
        listeners = [
            UDP_Listener(n, settings["Receiver_IP"], i)
            for n, i in enumerate(settings["Receivers"])
        ]
        Writer(settings, [i.fields for i in listeners], [i.connRecv for i in listeners])
    except BaseException as e:
        raise RuntimeError(f"Factory() {e}")


# main routine
def main():
    import os

    global g_ev

    # メインプロセスのロギング
    if not os.path.exists("./log"):
        os.mkdir("./log")  # ログフォルダ
    logging.basicConfig(filename="./log/UDP_Receive.log", level=logging.WARNING)
    try:
        Factory()  # UDP_ListenerとWriterの生成
        Stopper()  # 停止キー受付スレッド
        g_ev.wait()  # 通常実行時はここで他のスレッド・プロセスを待機
    except BaseException as e:
        print("abnormal end")
        logging.error(f"{datetime.datetime.now()} {type(e)} {e}")
    finally:
        print("main thread end")


# Windowsでマルチプロセスを実行するためにfreeze_support()を呼ぶ
if __name__ == "__main__":
    multiprocessing.freeze_support()
    g_ev = threading.Event()  # リスニング続行フラグ既定値False
    main()
