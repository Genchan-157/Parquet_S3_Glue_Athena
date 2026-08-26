# ParquetBatchConverter.py
# 文字コードをShift-JISからUTF-8に変換（必要なら）
# CSVファイルをParquetに変換
# デバッグしやすいシングルスレッド1回実行版

import datetime  # 日付処理
import decimal  # 10進固定
import json
import logging  # ログ出力
import shutil  # ファイル移動
import os  # ファイル削除
import csv  # CSV処理
import ast  # 二重引用符に囲まれたJSON解析用

# Pylanceエラー回避
from typing import Callable

# Parquet
import pyarrow
import pyarrow.csv
import pyarrow.parquet

INI = "converter.json"  # 同じフォルダにおいてある設定用JSONファイル

# ------------------------------------------------------------------------------#


# python 3.12でdisutilが廃止されたので代替関数
def strtobool(argstr: str) -> bool:
    a = argstr.lower()
    if a in ["y", "yes", "t", "true", "on", "1"]:
        return True
    if a in ["n", "no", "f", "false", "off", "0"]:
        return False
    raise ValueError


# 文字列からPyArrowのDataTypeを返す。引数なしで構築できる型のみ
def strtoDataType(arg: str) -> pyarrow.DataType:
    if arg == "string":
        return pyarrow.string()
    if arg == "bool":
        return pyarrow.bool_()
    if arg == "int8":
        return pyarrow.int8()
    if arg == "int16":
        return pyarrow.int16()
    if arg == "int32":
        return pyarrow.int32()
    if arg == "int64":
        return pyarrow.int64()
    if arg == "float32":
        return pyarrow.float32()
    if arg == "float64":
        return pyarrow.float64()
    if arg == "timestamp":
        return pyarrow.timestamp
    if arg == "date32":
        return pyarrow.date32()
    if arg == "date64":
        return pyarrow.date64()
    if arg == "time32":
        return pyarrow.time32()
    if arg == "time64":
        return pyarrow.time64()

    raise ValueError("strtoDataType() " + arg)


# ------------------------------------------------------------------------------#

# 日付処理補助関数


# yyyyMMdd 他とインターフェイスを揃えるため、デリミタを渡してよいことにする
def isodate_yyyyMMdd(arg: str, delimite: str = "") -> datetime.date:
    return datetime.date(int(arg[0:4]), int(arg[4:6]), int(arg[6:8]))


# yyyy/MM/dd
def isodate_ymd(arg: str, delimiter: str = "/") -> datetime.date:
    ymd = arg.split(delimiter)
    return datetime.date(int(ymd[0]), int(ymd[1]), int(ymd[2]))


# yyyy/MM
def isodate_ym(arg: str, delimiter: str = "/") -> datetime.date:
    ymd = arg.split(delimiter)
    return datetime.date(int(ymd[0]), int(ymd[1]), 1)


# mm/dd/yyyy
def isodate_mdy(arg: str, delimiter: str = "/") -> datetime.date:
    mdy = arg.split(delimiter)
    return datetime.date(int(mdy[2]), int(mdy[0]), int(mdy[1]))


# dd/mm/yyyy
def isodate_dmy(arg: str, delimiter: str = "/") -> datetime.date:
    dmy = arg.split(delimiter)
    return datetime.date(int(dmy[2]), int(dmy[1]), int(dmy[0]))


# mm/yyyy
def isodate_my(self, arg: str, delimiter: str = "/") -> datetime.date:
    my = arg.split(delimiter)
    return datetime.date(int(my[1]), int(my[0]), 1)


# ------------------------------------------------------------------------------#

# 例外を発生させないフィールド（継承しない）


# 入力を無視する列の型
class NoneField:
    def append_column(self, columns, key: str):
        pass

    def append_field(self, fields, key: str):
        pass

    def from_str(self, argstr: str = "") -> str:
        return ""


# 文字列型
class StringField:
    def append_column(self, columns, key: str):
        columns.append(key)

    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.string()))

    def from_str(self, argstr: str = "") -> str:
        return argstr


# boolean型 明らかにFalseのもの以外はTrueを返す
class BoolField:
    def append_column(self, columns, key: str):
        columns.append(key)

    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.bool_()))

    def from_str(self, argstr: str = "false") -> str:
        if argstr == "" or argstr == "None":
            return str(False)

        return str(strtobool(str.strip(argstr)))


# 10進型 2列出力は行わない
class Decimal128Field:
    def __init__(self, precision: int = 38, scale: int = 0):
        self.precision = precision
        self.scale = scale

    def append_column(self, columns, key: str):
        columns.append(key)

    def append_field(self, fields, key: str):
        fields.append(
            pyarrow.field(key, pyarrow.decimal128(self.precision, self.scale))
        )

    def from_str(self, argstr: str = "") -> str:
        if argstr == "NaN" or argstr == "Infinity" or argstr == "-Infinity":
            return ""  # Pythonのdecimalは処理できるが、PyArrowが文字列を処理できない
        try:
            return str(decimal.Decimal(argstr))
        except:
            return ""


# ------------------------------------------------------------------------------#


# 1列・1列出力フィールドの抽象基底クラス
class Field1I1O:
    def append_column(self, columns, key: str):
        columns.append(key)


# 1列入力・2列出力フィールドの抽象基底クラス
class Field1I2O:
    def append_column(self, columns, key: str):
        columns.append(key)
        columns.append(key + "_str")


# ------------------------------------------------------------------------------#


# 整数フィールド（基底クラス）
class IntegerField_Base:
    def __init__(self, minVal, maxVal):
        self.min = None if minVal is None else int(minVal)
        self.max = None if maxVal is None else int(maxVal)

    def convert(self, argstr: str = "") -> str:
        if argstr == "None" or argstr == "NaN" or len(argstr.strip()) == 0:
            return ""

        # 指数表記を探す
        n = argstr.find("E+")
        if n < 0:
            n = argstr.find("E-")
        if 0 <= n:
            mantissa = float(argstr[0:n])
            exponent = int(argstr[n + 1 :])
            ret = int(10**exponent * mantissa)
        else:
            ret = int(argstr.strip())

        if (self.min != None and ret < self.min) or (
            self.max != None and self.max < ret
        ):
            return ""

        return str(ret)  # str.isnumeric()は負数を判定できない


# 8bit整数型（基底クラス）
class Int8Field_Base(IntegerField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.int8()))


# 16bit整数型（基底クラス）
class Int16Field_Base(IntegerField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.int16()))


# 32bit整数型（基底クラス）
class Int32Field_Base(IntegerField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.int32()))


# 64bit整数型（基底クラス）
class Int64Field_Base(IntegerField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.int64()))


# 浮動小数点型（基底クラス）
class FloatingPointField_Base:
    def __init__(self, minVal, maxVal):
        self.min = None if minVal is None else float(minVal)
        self.max = None if maxVal is None else float(maxVal)
        # print( str(self.min) + ' : ' + str(self.max) )

    def convert(self, argstr: str = "") -> str:
        if argstr == "None" or argstr == "NaN":
            return ""

        # 指数表記を探す
        n = argstr.find("E+")
        if n < 0:
            n = argstr.find("E-")

        if 0 <= n:
            mantissa = float(argstr[0:n])
            exponent = int(argstr[n + 1 :])
            ret = 10.0**exponent * mantissa
        else:
            ret = float(argstr.strip())

        if (self.min != None and ret < self.min) or (
            self.max != None and self.max < ret
        ):
            return ""

        return str(ret)


# 単精度浮動小数点（基底クラス）
class Float32Field_Base(FloatingPointField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.float32()))


# 倍精度浮動小数点（基底クラス）
class Float64Field_Base(FloatingPointField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.float64()))


# ------------------------------------------------------------------------------#


# 例外をスローする整数型・浮動小数点型
class Int8Field(Field1I1O, Int8Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


class Int16Field(Field1I1O, Int16Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


class Int32Field(Field1I1O, Int32Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


class Int64Field(Field1I1O, Int64Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


class Float32Field(Field1I1O, Float32Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


class Float64Field(Field1I1O, Float64Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


# 例外は無視する整数型・浮動小数点型
class Int8Field_F(Field1I1O, Int8Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        try:
            return self.convert(argstr)
        except:
            return ""


class Int16Field_F(Field1I1O, Int16Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        try:
            return self.convert(argstr)
        except:
            return ""


class Int32Field_F(Field1I1O, Int32Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        try:
            return self.convert(argstr)
        except:
            return ""


class Int64Field_F(Field1I1O, Int64Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        try:
            return self.convert(argstr)
        except:
            return ""


class Float32Field_F(Field1I1O, Float32Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        try:
            return self.convert(argstr)
        except:
            return ""


class Float64Field_F(Field1I1O, Float64Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def from_str(self, argstr: str = "false") -> str:
        try:
            return self.convert(argstr)
        except:
            return ""


# ------------------------------------------------------------------------------#


# 数値でないものは文字列出力するする整数型・浮動小数点型
class IntegerField_S(Field1I2O, IntegerField_Base):
    def from_str(self, argstr: str = "false") -> str:
        ret = ["", ""]
        try:
            ret[0] = self.convert(argstr)
            if ret[0] == "":  # 範囲チェックで異常判定された場合
                ret[1] = argstr
            return "\t".join(ret)
        except:
            ret[1] = argstr
            return "\t".join(ret)


class Int8Field_S(IntegerField_S, Int8Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def append_field(self, fields, key: str):
        super().append_field(fields, key)
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))


class Int16Field_S(IntegerField_S, Int16Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def append_field(self, fields, key: str):
        super().append_field(fields, key)
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))


class Int32Field_S(IntegerField_S, Int32Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def append_field(self, fields, key: str):
        super().append_field(fields, key)
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))


class Int64Field_S(IntegerField_S, Int64Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def append_field(self, fields, key: str):
        super().append_field(fields, key)
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))


# 数値でないものは隣の列に出力する浮動小数点型（基底クラス）
class FloatingPointField_S(Field1I2O, FloatingPointField_Base):
    def from_str(self, argstr: str = "false") -> str:
        ret = ["", ""]
        try:
            ret[0] = self.convert(argstr)
            if ret[0] == "":  # 範囲チェックで異常判定された場合
                ret[1] = argstr
            return "\t".join(ret)
        except:
            ret[1] = argstr
            return "\t".join(ret)


class Float32Field_S(FloatingPointField_S, Float32Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def append_field(self, fields, key: str):
        super().append_field(fields, key)
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))


class Float64Field_S(FloatingPointField_S, Float64Field_Base):
    def __init__(self, minVal, maxVal):
        super().__init__(minVal, maxVal)

    def append_field(self, fields, key: str):
        super().append_field(fields, key)
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))


# ------------------------------------------------------------------------------#


# 32bit Time（日内）：抽象基底クラス
class Time32Field_Base:
    # 初期化時にタイムゾーンを取得
    def __init__(self, is_glue_field: bool):
        self.GlueField = is_glue_field

    # 変換関数
    def convert(self, argstr: str = "") -> str:
        if argstr == "" or argstr == "None" or argstr == "NaT":
            return ""
        hms = argstr.split(":")
        # print( hms )
        if len(hms) == 2:
            hms = [hms[0], hms[1], "00"]  # 秒がない列がある
        timeval = datetime.time(int(hms[0]), int(hms[1]), int(hms[2]))
        return timeval.strftime(
            "%H:%M:%S"
        )  # isoformat()では00秒の時に秒が省略され、Parquet変換で失敗する


# 例外あり32bit Time（日内）
class Time32Field(Field1I1O, Time32Field_Base):
    def __init__(self, is_glue_field: bool):
        super().__init__(is_glue_field)

    def append_field(self, fields, key: str):
        if self.GlueField:
            fields.append(pyarrow.field(key, pyarrow.string()))
        else:
            fields.append(pyarrow.field(key, pyarrow.time32("ms")))

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


# 例外なし2列出力32bit Time（日内）
class Time32Field_S(Field1I2O, Time32Field_Base):
    def __init__(self, is_glue_field: bool):
        super().__init__(is_glue_field)

    def append_field(self, fields, key: str):
        if self.GlueField:
            fields.append(pyarrow.field(key, pyarrow.string()))
        else:
            fields.append(pyarrow.field(key, pyarrow.time32("ms")))
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))

    def from_str(self, argstr: str = "false") -> str:
        ret = ["", ""]
        try:
            ret[0] = self.convert(argstr)
            return "\t".join(ret)
        except:
            ret[1] = argstr
            return "\t".join(ret)


# 32bit Date基底クラス
class Date32Field_Base:
    delimiter: str = "/"
    initialized = False
    fnConverter: Callable[[str, str], datetime.date] = (
        isodate_yyyyMMdd  # Pylanceエラー回避用に初期値設定
    )

    def __init__(self):
        if self.__class__.initialized:
            return
        with open(INI, "r") as fj:  # 自動クローズ
            settings = json.load(fj)
        self.__class__.delimiter = settings["input"]["DateTimeFormat"]["DateDelimiter"]
        if self.__class__.delimiter == "":
            self.__class__.fnConverter = isodate_yyyyMMdd  # 区切り無し8桁日付
        elif settings["input"]["DateTimeFormat"]["DateOrder"] == "mdy":
            self.__class__.fnConverter = isodate_mdy
        elif settings["input"]["DateTimeFormat"]["DateOrder"] == "dmy":
            self.__class__.fnConverter = isodate_dmy
        else:
            self.__class__.fnConverter = isodate_ymd
        self.__class__.initialized = True

    # 変換関数
    def convert(self, argstr: str = "") -> str:
        if argstr == "" or argstr == "None" or argstr == "NaT":
            return ""
        return self.__class__.fnConverter(argstr, self.__class__.delimiter).isoformat()


# 例外あり32bit Date
class Date32Field(Field1I1O, Date32Field_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.date32()))

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


# 例外なし2列出力32bit Date
class Date32Field_S(Field1I2O, Date32Field_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.date32()))
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))

    def from_str(self, argstr: str = "false") -> str:
        ret = ["", ""]
        try:
            ret[0] = self.convert(argstr)
            return "\t".join(ret)
        except:
            ret[1] = argstr
            return "\t".join(ret)


# タイムスタンプの抽象基底クラス
class TimeStampField_Base:
    timezone = "Z"
    delimiter = "-"
    DateTimeDelimiter = " "
    initialized = False
    fnConverter: Callable[[str, str], datetime.date] = (
        isodate_yyyyMMdd  # Pylanceエラー回避用に初期値設定
    )

    # 初期化時にタイムゾーンを取得
    def __init__(self):
        if self.__class__.initialized:
            return

        # json読み込み
        with open(INI, "r") as fj:  # 自動クローズ
            settings = json.load(fj)
        self.__class__.timezone = settings["input"]["timezone"]
        self.__class__.delimiter = settings["input"]["DateTimeFormat"]["DateDelimiter"]
        self.__class__.DateTimeDelimiter = settings["input"]["DateTimeFormat"][
            "DateTimeDelimiter"
        ]

        if self.__class__.delimiter == "":
            self.__class__.fnConverter = isodate_yyyyMMdd  # 区切り無し8桁日付
        elif settings["input"]["DateTimeFormat"]["DateOrder"] == "mdy":
            self.__class__.fnConverter = isodate_mdy
        elif settings["input"]["DateTimeFormat"]["DateOrder"] == "dmy":
            self.__class__.fnConverter = isodate_dmy
        else:
            self.__class__.fnConverter = isodate_ymd
        self.__class__.initialized = True


# yyyy/M/d hh:mm:ss の現在時刻（基底クラス）
class LoacalTimeStampField_Base(TimeStampField_Base):
    def convert(self, argstr: str = "") -> str:
        # 年月日が1桁の場合があるので、splitで対応
        if argstr == "" or argstr == "None" or argstr == "NaT":
            return ""
        a = argstr.split(self.__class__.DateTimeDelimiter)
        ymd = self.__class__.fnConverter(
            a[0], self.__class__.delimiter
        )  # 初期化時に設定した年月日の解釈を行う（実行時に分岐すると複雑化する方向）
        if len(a) >= 2:
            hms = a[1].split(":")
        else:
            hms = ["00", "00", "00"]

        if len(hms) == 2:
            hms = [hms[0], hms[1], "00"]
        datetimeval = datetime.datetime(
            ymd.year, ymd.month, ymd.day, int(hms[0]), int(hms[1]), int(hms[2])
        )
        return datetimeval.strftime("%Y-%m-%dT%H:%M:%S") + self.__class__.timezone


# 例外ありタイムゾーン付きの時刻（基本型）
class LocalTimeStampField(Field1I1O, LoacalTimeStampField_Base):
    # 時差はここで取得し、UTCとの差を修正したタイムスタンプに補正する
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.timestamp("us", self.timezone)))

    def from_str(self, argstr: str = "false") -> str:
        return self.convert(argstr)


# 例外なし2列出力タイムゾーン付きの時刻
class LocalTimeStampField_S(Field1I2O, LoacalTimeStampField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.timestamp("us", self.timezone)))
        fields.append(pyarrow.field(key + "_str", pyarrow.string()))

    def from_str(self, argstr: str = "false") -> str:
        ret = ["", ""]
        try:
            ret[0] = self.convert(argstr)
            return "\t".join(ret)
        except:
            ret[1] = argstr
            return "\t".join(ret)


# 例外ありISO形式のローカル時刻
class LocalTimeIsoField(Field1I1O, TimeStampField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.timestamp("us", self.timezone)))

    def from_str(self, argstr: str = "") -> str:
        try:
            return argstr + self.timezone
        except Exception as e:
            raise ValueError("LocalTimeIsoField.from_str() " + str(e))


# Python実行環境の時間
class UtcTimeStampField(Field1I1O, TimeStampField_Base):
    def append_field(self, fields, key: str):
        fields.append(pyarrow.field(key, pyarrow.timestamp("us", "UTC")))

    def from_str(self, argstr: str = "") -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ------------------------------------------------------------------------------#


# array, map struct
class ComplexField_Base(StringField):
    def __init__(self, is_glue_field: bool, fieldname: str):
        super().__init__()
        self.name = fieldname
        self.GlueField = is_glue_field

    def modify(self, table: pyarrow.table) -> pyarrow.table:
        return table


class ArrayField(ComplexField_Base):
    def __init__(self, is_glue_field: bool, fieldname: str, datatype: str):
        super().__init__(is_glue_field, fieldname)
        self.datatype = strtoDataType(datatype)

    def modify(self, table: pyarrow.table) -> pyarrow.table:
        if self.GlueField:
            return table
        lst = [ast.literal_eval(i.as_py()) for i in table.column(self.name)]
        # print(lst)
        table.drop_columns(self.name)
        return table.append_column(
            self.name, pyarrow.array(lst, pyarrow.list_(self.datatype))
        )


class MapField(ComplexField_Base):
    def __init__(
        self,
        is_glue_field: bool,
        fieldname: str,
        key_type,
        item_type,
        keys_sorted: bool,
    ):
        super().__init__(is_glue_field, fieldname)
        self.key_type = strtoDataType(key_type)
        self.item_type = strtoDataType(item_type)
        self.keys_sorted = keys_sorted

    def modify(self, table: pyarrow.table) -> pyarrow.table:
        if self.GlueField:
            return table
        # {'key':'x', 'value':10}と'key', 'value'という名前が必須
        map = [ast.literal_eval(i.as_py()) for i in table.column(self.name)]
        # print(map)
        table.drop_columns(self.name)
        return table.append_column(
            self.name,
            pyarrow.array(
                map,
                pyarrow.map_(self.key_type, self.item_type, self.keys_sorted),
            ),
        )


class StructField(ComplexField_Base):
    def __init__(self, is_glue_field: bool, fieldname: str, fields: list):
        super().__init__(is_glue_field, fieldname)
        self.fields = [(f["name"], strtoDataType(f["type"])) for f in fields]
        # print(self.fields)

    def modify(self, table: pyarrow.table) -> pyarrow.table:
        if self.GlueField:
            return table
        struct = [ast.literal_eval(i.as_py()) for i in table.column(self.name)]
        print(struct)
        table.drop_columns(self.name)
        return table.append_column(
            self.name,
            pyarrow.array(struct, pyarrow.struct(self.fields)),
        )


# ------------------------------------------------------------------------------#


# 特殊フィールド00 ファイルフォーマットによって適宜定義する
class Special_00Field:
    def append_column(self, columns, key: str):
        columns.append(key + "_0")
        columns.append(key + "_1")

    def append_field(self, fields, key: str):
        # 返却予定の値の分だけフィールドを追加する
        fields.append(pyarrow.field(key + "_0", pyarrow.string()))
        fields.append(pyarrow.field(key + "_1", pyarrow.string()))

    def from_str(self, argstr: str) -> str:
        try:
            # ToDo: 文字列を分解して、複数のフィールド値にする

            # フィールドをタブで連結して返す
            return argstr.replace("_", "\t")
        except BaseException as e:
            raise ValueError("Special_00Field.from_str() " + str(e))


# 特殊フィールド01 ファイルフォーマットによって適宜定義する
class Special_01Field:
    def append_column(self, columns, key: str):
        columns.append(key + "_0")
        columns.append(key + "_1")

    def append_field(self, fields, key: str):
        # 返却予定の値の分だけフィールドを追加する
        fields.append(pyarrow.field(key + "_0", pyarrow.string()))
        fields.append(pyarrow.field(key + "_1", pyarrow.string()))

    def from_str(self, argstr: str) -> str:
        try:
            # ToDo: 文字列を分解して、複数のフィールド値にする

            # フィールドをタブで連結して返す
            return argstr.replace("/", "\t")
        except BaseException as e:
            raise ValueError("Special_01Field.from_str() " + str(e))


# 特殊フィールド02 ファイルフォーマットによって適宜定義する
class Special_02Field:
    def append_column(self, columns, key: str):
        columns.append(key + "_0")
        columns.append(key + "_1")

    def append_field(self, fields, key: str):
        # 返却予定の値の分だけフィールドを追加する
        fields.append(pyarrow.field(key + "_0", pyarrow.string()))
        fields.append(pyarrow.field(key + "_1", pyarrow.string()))

    def from_str(self, argstr: str) -> str:
        try:
            # ToDo: 文字列を分解して、複数のフィールド値にする

            # フィールドをタブで連結して返す
            return argstr.replace("  ", "\t")
        except BaseException as e:
            raise ValueError("Special_02Field.from_str() " + str(e))


# 特殊フィールド03 ファイルフォーマットによって適宜定義する
class Special_03Field:
    def append_column(self, columns, key: str):
        columns.append(key + "_0")
        columns.append(key + "_1")

    def append_field(self, fields, key: str):
        # 返却予定の値の分だけフィールドを追加する
        fields.append(pyarrow.field(key + "_0", pyarrow.string()))
        fields.append(pyarrow.field(key + "_1", pyarrow.string()))

    def from_str(self, argstr: str) -> str:
        try:
            # ToDo: 文字列を分解して、複数のフィールド値にする

            # フィールドをタブで連結して返す
            return argstr.replace("_", "\t")
        except BaseException as e:
            raise ValueError("Special_03Field.from_str() " + str(e))


# 特殊フィールド04 ファイルフォーマットによって適宜定義する
class Special_04Field:
    def append_column(self, columns, key: str):
        columns.append(key + "_0")
        columns.append(key + "_1")

    def append_field(self, fields, key: str):
        # 返却予定の値の分だけフィールドを追加する
        fields.append(pyarrow.field(key + "_0", pyarrow.string()))
        fields.append(pyarrow.field(key + "_1", pyarrow.string()))

    def from_str(self, argstr: str) -> str:
        try:
            # ToDo: 文字列を分解して、複数のフィールド値にする

            # フィールドをタブで連結して返す
            return argstr.replace("-", "\t")
        except BaseException as e:
            raise ValueError("Special_04Field.from_str() " + str(e))


# ------------------------------------------------------------------------------#


# ファイル書き込みスレッド
class Writer:
    # インスタンス毎に持つ必要のない変数 isinstance()を使うためにインスタンス化()
    noneField = NoneField()
    strField = StringField()
    boolField = BoolField()

    initialized = False  # クラス変数初期化済みフラグ
    Fields = list()  # CSVファイルの列リスト
    colNames = list()
    complexFields = list()  # 複合列のリスト
    parquetSchemas = None  # Parquetのスキーマ
    inputEncoding = "utf-8"
    headerRows = 0  # ヘッダの行数
    notNoneFields = 0  # None型以外のフィールド

    def __init__(self, settings: dict):
        self.filehandle = None
        self.basefilename = None  # 拡張子なしのファイル名
        is_glue_field = settings["AWS"]["GlueFields"]

        if self.__class__.initialized:
            return
        # json読み込み
        self.__class__.inputEncoding = settings["input"]["encoding"]
        self.__class__.headerRows = int(settings["input"]["headerRows"])

        global inputDir
        inputDir = settings["Directories"]["Input"]
        if os.path.exists(inputDir) == False:
            os.mkdir(inputDir)
        global tempDir
        tempDir = "./temp"
        if os.path.exists(tempDir) == False:
            os.mkdir(tempDir)
        global outputDir
        outputDir = settings["Directories"]["Output"]
        if os.path.exists(outputDir) == False:
            os.mkdir(outputDir)
        global convertedDir
        convertedDir = settings["Directories"]["Converted"]
        if os.path.exists(convertedDir) == False:
            os.mkdir(convertedDir)

        # 各フィールド情報の初期化
        fieldlist = list()
        v = settings["fields"]
        for f in v:
            o = f["type"]
            # print(f) for debug

            if o == "None":
                self.register(self.__class__.noneField, fieldlist, f["name"])
            else:
                self.__class__.notNoneFields += 1
                if o == "string":
                    self.register(self.__class__.strField, fieldlist, f["name"])
                elif o == "bool":
                    self.register(self.__class__.boolField, fieldlist, f["name"])
                elif o == "decimal":
                    self.register(
                        Decimal128Field(f["precision"], f["scale"]),
                        fieldlist,
                        f["name"],
                    )
                elif o[0:3] == "int":
                    self.register(self.CreateIntField(f), fieldlist, f["name"])
                elif o[0:5] == "float":
                    self.register(self.CreateFloatField(f), fieldlist, f["name"])
                elif o == "time":
                    self.register(Time32Field(is_glue_field), fieldlist, f["name"])
                elif o == "time_s":
                    self.register(Time32Field_S(is_glue_field), fieldlist, f["name"])
                elif o == "date":
                    self.register(Date32Field(), fieldlist, f["name"])
                elif o == "date_s":
                    self.register(Date32Field_S(), fieldlist, f["name"])
                elif o == "localTime":
                    self.register(LocalTimeStampField(), fieldlist, f["name"])
                elif o == "localTime_s":
                    self.register(LocalTimeStampField_S(), fieldlist, f["name"])
                elif o == "localTimeIso":
                    self.register(LocalTimeIsoField(), fieldlist, f["name"])
                elif o == "utcTime":
                    self.register(UtcTimeStampField(), fieldlist, f["name"])
                elif o == "list":  # 複合型は一旦Stringで読み込む
                    cf = ArrayField(is_glue_field, f["name"], f["data_type"])
                    self.__class__.complexFields.append(cf)
                    self.register(cf, fieldlist, f["name"])
                elif o == "map":  # 複合型は一旦Stringで読み込む
                    cf = MapField(
                        is_glue_field,
                        f["name"],
                        f["key_type"],
                        f["item_type"],
                        f["keys_sorted"],
                    )
                    self.__class__.complexFields.append(cf)
                    self.register(cf, fieldlist, f["name"])
                elif o == "struct":  # 複合型は一旦Stringで読み込む
                    cf = StructField(is_glue_field, f["name"], f["fields"])
                    self.__class__.complexFields.append(cf)
                    self.register(cf, fieldlist, f["name"])
                elif o == "special00":
                    self.register(Special_00Field(), fieldlist, f["name"])
                elif o == "special01":
                    self.register(Special_01Field(), fieldlist, f["name"])
                elif o == "special02":
                    self.register(Special_02Field(), fieldlist, f["name"])
                elif o == "special03":
                    self.register(Special_03Field(), fieldlist, f["name"])
                elif o == "special04":
                    self.register(Special_04Field(), fieldlist, f["name"])
                else:
                    raise ValueError(
                        "schema.json field type must be [utcTime, localTime(_s,Iso), time(_s), date(_s), string, bool, decimal, int{8,16,32,64}(_f,_s) float{32,64}(_f,_s), list, map, struct, special[00-04]"
                    )
        self.__class__.parquetSchemas = pyarrow.schema(fieldlist)
        self.__class__.initialized = True

    # intフィールドを作成して返す
    def CreateIntField(self, fdef: dict):
        min = int(fdef["min"]) if "min" in fdef and not fdef["min"] is None else None
        max = int(fdef["max"]) if "max" in fdef and not fdef["max"] is None else None

        if fdef["type"] == "int8":
            return Int8Field(min, max)
        elif fdef["type"] == "int8_f":
            return Int8Field_F(min, max)
        elif fdef["type"] == "int8_s":
            return Int8Field_S(min, max)
        elif fdef["type"] == "int16":
            return Int16Field(min, max)
        elif fdef["type"] == "int16_f":
            return Int16Field_F(min, max)
        elif fdef["type"] == "int16_s":
            return Int16Field_S(min, max)
        elif fdef["type"] == "int32":
            return Int32Field(min, max)
        elif fdef["type"] == "int32_f":
            return Int32Field_F(min, max)
        elif fdef["type"] == "int32_s":
            return Int32Field_S(min, max)
        elif fdef["type"] == "int64":
            return Int64Field(min, max)
        elif fdef["type"] == "int64_f":
            return Int64Field_F(min, max)
        elif fdef["type"] == "int64_s":
            return Int64Field_S(min, max)
        else:
            raise ValueError(
                "Writer.CreateIntfield("
                + fdef["type"]
                + "): invalid field definition in schema.json"
            )

    # floatフィールドを作成して返す
    def CreateFloatField(self, fdef: dict):
        min = float(fdef["min"]) if "min" in fdef and not fdef["min"] is None else None
        max = float(fdef["max"]) if "max" in fdef and not fdef["max"] is None else None

        if fdef["type"] == "float32":
            return Float32Field(min, max)
        elif fdef["type"] == "float32_f":
            return Float32Field_F(min, max)
        elif fdef["type"] == "float32_s":
            return Float32Field_S(min, max)
        elif fdef["type"] == "float64":
            return Float64Field(min, max)
        elif fdef["type"] == "float64_f":
            return Float64Field_F(min, max)
        elif fdef["type"] == "float64_s":
            return Float64Field_S(min, max)
        else:
            raise ValueError(
                "Writer.CreateFloatField("
                + fdef["type"]
                + "): invalid field definition in schema.json"
            )

    # フィールドごとの登録関数
    def register(self, f, fieldlist: list, key: str):
        # append_field()とappend_column()はダックタイピングで呼び出し
        self.__class__.Fields.append(f)
        f.append_field(fieldlist, key)
        f.append_column(self.__class__.colNames, key)

    # ファイル走査処理の起動
    def WaitEvent(self):
        try:
            self.ConvertFiles()
        except BaseException as e:
            logging.error(
                str(datetime.datetime.now()) + "\t Writer.WaitEvent() " + str(e)
            )

    # 書き込みThread：ファイルの走査
    def ConvertFiles(self):
        global inputDir
        files = os.listdir(inputDir)
        for inputFile in files:
            # 入力ファイル名の制限
            # if re.search(r'国内売上実績',inputFile) == None:
            #    continue

            # print(inputFile)
            tsvPath = self.CsvToTsv(inputFile)
            self.ConvertToParquet(tsvPath)
            shutil.move(
                inputDir + "/" + inputFile, convertedDir + "/" + inputFile
            )  # 2回処理しないように移動
            os.remove(tsvPath)  # 一時ファイルを削除

    # CSVファイルをTSVに変換
    def CsvToTsv(self, csvFile):
        rowcnt = 0
        colcnt = 0
        fldcnt = 0
        maxColumns = len(self.__class__.Fields)

        try:
            n = csvFile.rindex(".")
            csvPath = inputDir + "/" + csvFile
            tsvPath = tempDir + "/" + str(csvFile)[0:n] + ".tsv"

            with open(csvPath, mode="r", encoding=self.__class__.inputEncoding) as cf:
                # フォーマットに合わせて、いずれかのreaderを有効にする
                # reader = csv.reader(cf, delimiter='\t', quoting=csv.QUOTE_NONE)    # tsv
                reader = csv.reader(cf, dialect="excel")  # ExcelのCSV

                with open(tsvPath, mode="w", newline="", encoding="utf-8") as tf:
                    tf.write("\t".join(self.__class__.colNames) + "\r\n")

                    for row in reader:
                        rowcnt += 1
                        if rowcnt <= self.__class__.headerRows:
                            continue

                        # 行スキップ条件
                        # if re.fullmatch(r'[A-Z0-9]+', row[0]) == None:
                        #    continue

                        line = [""] * self.__class__.notNoneFields  # 出力用リスト
                        colcnt = 0  # 読み込み列カウンタ
                        fldcnt = 0  # 書き込み列カウンタ
                        # 各列の処理
                        for c in row:
                            # NoneFieldは無視する
                            if isinstance(self.__class__.Fields[colcnt], NoneField):
                                colcnt += 1
                                continue
                            line[fldcnt] = str(
                                self.__class__.Fields[colcnt].from_str(
                                    str(c).replace("\r", "").replace("\n", "")
                                )
                            )  # ダックタイピングでfrom_str()呼び出し

                            colcnt += 1  # 列番号加算
                            fldcnt += 1  # 出力列番号加算
                            if (
                                self.__class__.notNoneFields <= fldcnt
                                or maxColumns <= colcnt
                            ):
                                break  # Overflow対策

                        # 行末尾の処理
                        tf.write("\t".join(line) + "\r\n")  # 1行書き込み
            return tsvPath
        except BaseException as e:
            raise ValueError(
                "Writer.CsvToTsv(r="
                + str(rowcnt)
                + " , c="
                + str(colcnt + 1)
                + ") "
                + self.__class__.colNames[fldcnt]
                + " "
                + str(e)
            )

    # Parquetファイルの作成。関数終了時に元ファイルを閉じたいので別関数に切り出す
    def ConvertToParquet(self, tsvPath):
        # print ('Parquet ' + filename )
        # ToDo:入力ファイル命名規則から、yyyyMMdd_（同じ日重複しない体系）.parquet で命名できるように修正すること。
        try:
            filename = tsvPath[tsvPath.rindex("/") + 1 :]
            n = filename.rindex(".")
            output_file = outputDir + "/" + filename[0:n] + ".parquet"

            arrow_table = self.get_pyarrow_table(tsvPath)
            for cf in self.__class__.complexFields:
                arrow_table = cf.modify(arrow_table)

            pyarrow.parquet.write_table(
                arrow_table,
                output_file,
                compression="snappy",  # snappyで圧縮
                flavor=["spark"],  # spark互換の設定
            )
        except BaseException as e:
            raise ValueError("ConvertToParquet() " + str(e))

    # Tableオブジェクトを返す
    # https://dev.classmethod.jp/articles/20190614-apache-arrow-parquet/
    def get_pyarrow_table(self, input_files: str) -> pyarrow.Table:
        try:
            readoptions = pyarrow.csv.ReadOptions(
                use_threads=True,  # 複数の読み取りスレッドの利用
                block_size=1048576,  # 読み取りブロック数(1MB)
            )

            convertoptions = pyarrow.csv.ConvertOptions(
                check_utf8=True,  # 文字列カラムのUTF-8妥当性をチェック
                column_types=self.__class__.parquetSchemas,  # 列のデータ型をschemaで渡す
                null_values=[""],  # データ内のNULLを表す文字列
            )

            parseoptions = pyarrow.csv.ParseOptions(
                delimiter="\t",  # タブ区切り指定
                quote_char=False,  # 引用符で囲まない
                double_quote=False,  # ダブルクオートで括らない
                escape_char=False,  # エスケープ文字の指定
            )

            pyarrow_table = pyarrow.csv.read_csv(
                input_file=input_files,
                read_options=readoptions,
                parse_options=parseoptions,
                convert_options=convertoptions,
            )
            return pyarrow_table
        except BaseException as e:
            raise ValueError("get_pyarrow_table() " + str(e))


# ------------------------------------------------------------------------------#

# main routine
try:
    logging.basicConfig(filename="ParquetBatchConverter.log", level=logging.INFO)

    # jsonからグローバル変数を初期化
    with open(INI, "r") as fj:  # 自動クローズ
        settings = json.load(fj)

    # ライターを初期化
    wtr0 = Writer(settings)
    wtr0.WaitEvent()  # シングルスレッドなので、明示的な呼び出しが必要

except BaseException as e:
    logging.error(str(datetime.datetime.now()) + "\t" + str(e))
    print("abnormal end")
finally:
    print("main thread end")
