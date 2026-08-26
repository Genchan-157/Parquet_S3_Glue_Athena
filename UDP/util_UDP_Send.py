# 2025/07/15 util_UDP_Send.py
# 1回の通信で複数行受信に対応。5ms×10行を100ms単位で受信といった使用法に対応

import time  # sleep
import datetime  # datetime
import ctypes
import struct  # float->bin	https://docs.python.org/3/library/struct.html
import socket
import json
import random  # 乱数発生
import math  # 三角関数
import threading
import array
import signal
import platform
import logging

INI = "settings.json"  # 同じフォルダにおいてある設定用JSONファイル
g_loop = True

# --------------------------------------------------------------------------#


# int16
class Int16Field:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 2] = int(
            (10000)
            * math.sin((dt.second * 1000000 + dt.microsecond) * math.pi / 30000000)
        ).to_bytes(2, byteorder="little", signed=True)
        index[0] += 2


# int32
class Int32Field:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 4] = int(
            (10000)
            * math.cos((dt.second * 1000000 + dt.microsecond) * math.pi / 30000000)
        ).to_bytes(4, byteorder="little", signed=True)
        index[0] += 4


# int64
class Int64Field:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 8] = int(
            (100000)
            * math.cos((dt.second * 1000000 + dt.microsecond) * math.pi / 30000000)
        ).to_bytes(8, byteorder="little", signed=True)
        index[0] += 8


# float32
class Float32Field:
    def __init__(self):
        self.val = random.random()

    def get_bytes(self, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 4] = struct.pack("<f", self.val)
        self.val = (
            self.val + (2 * self.val**2)
            if self.val <= 0.5
            else self.val - 2 * (1 - self.val) ** 2
        )

        index[0] += 4


# float64
class Float64Field:
    def __init__(self):
        self.val = random.random()

    def get_bytes(self, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 8] = struct.pack("<d", self.val)
        self.val = 3.95 * self.val * (1 - self.val)
        index[0] += 8


# bool16
class Bool16Field:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 2] = (
            dt.minute // 10 << 12
            | dt.minute % 10 << 8
            | dt.second // 10 << 4
            | dt.second % 10
        ).to_bytes(2, byteorder="little", signed=False)
        index[0] += 2


# bool32
class Bool32Field:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 4] = (
            dt.day // 10 % 10 << 28
            | dt.day % 10 << 24
            | dt.hour // 10 << 20
            | dt.hour % 10 << 16
            | dt.minute // 10 << 12
            | dt.minute % 10 << 8
            | dt.second // 10 << 4
            | dt.second % 10
        ).to_bytes(4, byteorder="little", signed=False)
        index[0] += 4


# datetime DATERD命令
class MelsecDateTime:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 2] = dt.year.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 2 : index[0] + 4] = dt.month.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 4 : index[0] + 6] = dt.day.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 6 : index[0] + 8] = dt.hour.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 8 : index[0] + 10] = dt.minute.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 10 : index[0] + 12] = dt.second.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 12 : index[0] + 14] = dt.weekday().to_bytes(
            2, byteorder="little", signed=True
        )
        index[0] += 14


# datetime+ms S.DATERD命令
class MelsecDateTimeMs:
    @classmethod
    def get_bytes(cls, dt: datetime.datetime, view: memoryview, index: list[int]):
        view[index[0] : index[0] + 2] = dt.year.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 2 : index[0] + 4] = dt.month.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 4 : index[0] + 6] = dt.day.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 6 : index[0] + 8] = dt.hour.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 8 : index[0] + 10] = dt.minute.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 10 : index[0] + 12] = dt.second.to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 12 : index[0] + 14] = dt.weekday().to_bytes(
            2, byteorder="little", signed=True
        )
        view[index[0] + 14 : index[0] + 16] = (dt.microsecond // 1000).to_bytes(
            2, byteorder="little", signed=True
        )
        index[0] += 16


# 文字列フィールドクラス群
class AsciiField:
    def __init__(self, bytelen: int):
        self.bytes = bytelen

    def get_bytes(self, dt: datetime.datetime, view: memoryview, index: list[int]):
        ctypes.memset(
            (ctypes.c_ubyte * self.bytes).from_buffer(view, index[0]),
            0,
            self.bytes,
        )
        bs = dt.strftime("%Y/%m/%d %H:%M:%S").encode("ascii")
        view[index[0] : index[0] + len(bs)] = bs
        index[0] += self.bytes


class SjisField:
    def __init__(self, bytelen: int):
        self.bytes = bytelen

    def get_bytes(self, dt: datetime.datetime, view: memoryview, index: list[int]):
        ctypes.memset(
            (ctypes.c_ubyte * self.bytes).from_buffer(view, index[0]),
            0,
            self.bytes,
        )
        mystr = "只今%Y年%m月%d日 %H時%M分%S秒"
        mo = (dt.minute * 60 + dt.second) % 7
        if mo == 0:
            mystr += "。"
        elif mo == 1:
            mystr += "。。"
        elif mo == 2:
            mystr += "。。。"
        elif mo == 3:
            mystr += "。。。。"
        elif mo == 4:
            mystr += "。。。。。"
        elif mo == 5:
            mystr += "。。。。。。"
        elif mo == 6:
            mystr += "。。。。。。。"

        bs = dt.strftime(mystr).encode("shift_jis")
        view[index[0] : index[0] + len(bs)] = bs
        index[0] += self.bytes


class Utf16leField:
    def __init__(self, bytelen: int):
        self.bytes = bytelen

    def get_bytes(self, dt: datetime.datetime, view: memoryview, index: list[int]):
        ctypes.memset(
            (ctypes.c_ubyte * self.bytes).from_buffer(view, index[0]),
            0,
            self.bytes,
        )
        mystr = "只今%Y年%m月%d日 %H時%M分%S秒"
        mo = (dt.minute * 60 + dt.second) % 7
        if mo == 0:
            mystr += "。"
        elif mo == 1:
            mystr += "。。"
        elif mo == 2:
            mystr += "。。。"
        elif mo == 3:
            mystr += "。。。。"
        elif mo == 4:
            mystr += "。。。。。"
        elif mo == 5:
            mystr += "。。。。。。"
        elif mo == 6:
            mystr += "。。。。。。。"
        bs = dt.strftime(mystr).encode("utf_16_le")
        view[index[0] : index[0] + len(bs)] = bs
        index[0] += self.bytes


# --------------------------------------------------------------------------#


# 送信クラス
class UDP_Sender:
    def __init__(self, ip: str, receiver: dict):
        # フィールドの読み込みと初期化
        self.msgLen = array.array("i", [0])
        self.fldLen = array.array("h", [0])
        self.fields = list()
        v = receiver["fields"]

        for f in v:
            # print(f["name"])
            if f["type"] == "int16":
                self.fldLen[0] += 1
                self.msgLen[0] += 2
                self.fields.append(Int16Field())
            elif f["type"] == "int32":
                self.fldLen[0] += 1
                self.msgLen[0] += 4
                self.fields.append(Int32Field())
            elif f["type"] == "int64":
                self.fldLen[0] += 1
                self.msgLen[0] += 8
                self.fields.append(Int64Field())
            elif f["type"] == "float32":
                self.fldLen[0] += 1
                self.msgLen[0] += 4
                self.fields.append(Float32Field())
            elif f["type"] == "float64":
                self.fldLen[0] += 1
                self.msgLen[0] += 8
                self.fields.append(Float64Field())
            elif f["type"] == "bool16":
                self.fldLen[0] += 1
                self.msgLen[0] += 2
                self.fields.append(Bool16Field())
            elif f["type"] == "bool32":
                self.fldLen[0] += 1
                self.msgLen[0] += 4
                self.fields.append(Bool32Field())
            elif f["type"] == "shift_jis":
                self.fldLen[0] += 1
                self.msgLen[0] += int(f["bytes"])
                self.fields.append(SjisField(int(f["bytes"])))
            elif f["type"] == "ascii":
                self.fldLen[0] += 1
                self.msgLen[0] += int(f["bytes"])
                self.fields.append(AsciiField(int(f["bytes"])))
            elif f["type"] == "utf_16_le":
                self.fldLen[0] += 1
                self.msgLen[0] += int(f["bytes"])
                self.fields.append(Utf16leField(int(f["bytes"])))
            elif f["type"] == "datetime":
                self.fldLen[0] += 1
                self.msgLen[0] += 14
                self.fields.append(MelsecDateTime)
            elif f["type"] == "datetime_ms":
                self.fldLen[0] += 1
                self.msgLen[0] += 16
                self.fields.append(MelsecDateTimeMs)
            elif f["type"] == "utcTime":
                pass  # 受信側で生成するフィールド
            elif f["type"] == "localTime":
                pass  # 受信側で生成するフィールド
            else:
                raise ValueError("field type " + f["type"] + " is not defined.")

        # self.msgLen[0]を繰り返し回数に合わせて増やす
        self.field_repeat = (
            int(receiver["field_repeat"]) if "field_repeat" in receiver else 1
        )
        self.msgLen[0] *= self.field_repeat
        print(f"field repeat = {self.field_repeat}")

        #  ポート設定
        self.msg = bytearray(self.msgLen[0])
        self.view = memoryview(self.msg)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Internet  # UDP
        self.UDP_IP = ip
        self.UDP_PORT = int(receiver["UDP_Port"])
        print("UDP target " + str(self.UDP_IP) + ":" + str(self.UDP_PORT))

        # マルチスレッド開始
        self.ev = threading.Event()
        self.th = threading.Thread(target=lambda: self.WaitEvent(), daemon=True)
        self.th.start()

    def send(self):
        self.ev.set()

    def WaitEvent(self):
        global g_loop

        self.fldCnt = array.array("h", [0])
        self.index = array.array("i", [0])
        self.repeat = array.array("h", [0])

        try:
            while g_loop:
                self.ev.clear()
                self.ev.wait()
                self.index[0] = 0

                # 繰り返し回数に合わせてループ回数を増やす
                self.repeat[0] = 0
                while self.repeat[0] < self.field_repeat:
                    dt = datetime.datetime.now()
                    self.fldCnt[0] = 0
                    while self.fldCnt[0] < self.fldLen[0]:
                        self.fields[self.fldCnt[0]].get_bytes(dt, self.view, self.index)
                        self.fldCnt[0] += 1
                    self.repeat[0] += 1
                    # time.sleep(0.05)    # ループがあまりにも速いので確認用のスリープ
                self.sock.sendto(self.msg, (self.UDP_IP, self.UDP_PORT))
                # print(datetime.datetime.now())
        except BaseException as e:
            raise RuntimeError(f"send() ErrorFieldIndex = {self.fldCnt[0]} {e}")


# ------------------------------------------------------------------------------#
# Stop key Input
class Stopper:
    def __init__(self):
        # マルチスレッド処理
        self.th = threading.Thread(target=lambda: self.WaitEvent(), daemon=True)
        self.th.start()

    def WaitEvent(self):
        global logging
        global g_ev
        try:
            while True:
                keyin = input("Input 'x' and 'Enter' if you want to stop sending.\n")
                if keyin == "x":
                    g_ev.set()
                    break
        except EOFError:
            pass  # コンソールなし -> 何もせずにスレッド終了
        except BaseException as e:
            logging.error(str(datetime.datetime.now()) + "\t" + str(e))


# --------------------------------------------------------------------------#
# main routine
g_ev = threading.Event()
try:
    with open(INI, "r") as fj:  # 自動クローズ
        settings = json.load(fj)
    IP = settings["Receiver_IP"]
    sender = [UDP_Sender(IP, i) for i in settings["Receivers"]]
    stopper = Stopper()  # 停止キー受付スレッド

    interval = 3.0  # second
    if platform.system() == "Windows":
        # for windows
        print("Windows System")
        next = datetime.datetime.now()
        delta = datetime.timedelta(microseconds=interval * 1000000)
        while not g_ev.is_set():
            next += delta
            [i.send() for i in sender]
            s = next - datetime.datetime.now()
            time.sleep((s.microseconds + s.seconds * 1000000) / 1000000.0)
    else:
        # for UNIX
        print("UNIX System")
        signal.signal(signal.SIGALRM, lambda a, b: [i.send() for i in sender])
        signal.setitimer(signal.ITIMER_REAL, interval, interval)
        g_ev.clear()
        g_ev.wait()  # 通常実行時はここで他のスレッド・プロセスを待機

except BaseException as e:
    print("abnormal end. " + str(e))
