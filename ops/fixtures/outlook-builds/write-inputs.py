#!/usr/bin/env python3
"""Reproduce synthetic HxStore bytes without invoking or importing hxprobe."""
from pathlib import Path
import struct
import zlib

HERE = Path(__file__).resolve().parent


def put(data, at, fmt, value):
    struct.pack_into("<" + fmt, data, at, value)


def identity(data, at, cls, key, kind, owner):
    data[at:at + 46] = struct.pack("<H8xQIQQQ", cls, key, kind, owner, key, owner)


def text(value):
    return (value + "\0").encode("utf-16le")


def slots(data, values):
    heap = bytearray()
    for at, value in values:
        struct.pack_into("<II", data, at, len(heap), len(value) | 0x80000000)
        heap.extend(value)
    return data + heap


def envelope(tag, data):
    return struct.pack("<HHIH", 5, tag, len(data) + 10, 0) + data


def message(key, subject):
    data = bytearray(1541)
    identity(data, 0, 0xc9, key, 212, 7)
    identity(data, 888, 0x4d, 50, 2, 7)
    for at in (278, 366, 718):
        put(data, at, "Q", 639000000000000000)
    return envelope(0x060f, slots(data, [(1226, text("IPM.Note")),
        (1294, text("Synthetic preview has four words.")),
        (1386, text("sender@example.test")), (1422, text(subject))]))


def payload(tag):
    folder = bytearray(840)
    identity(folder, 0, 0x4d, 50, 2, 7)
    data = envelope(tag, folder + text("Synthetic Inbox"))
    data += message(70, "Full synthetic mail")
    data += message(71, "Conflicting subject A") + message(71, "Conflicting subject B")
    for key, kind, address, name in [(80, 204, "to@example.test", "To"),
            (81, 205, "cc@example.test", "Cc"), (82, 206, "bcc@example.test", "Bcc")]:
        recipient = bytearray(340)
        identity(recipient, 0, 0x55, key, kind, 70)
        data += envelope(0x015e, slots(recipient, [(282, text(name)), (290, text(address))]))
    body = bytearray(1860)
    identity(body, 0, 0xca, 70, 0, 70)
    identity(body, 48, 0xc9, 70, 212, 7)
    data += envelope(0x074e, slots(body, [(1650, b"<p>Full synthetic body with Unicode caf\xc3\xa9.</p>")]))
    for key, flag, due, completed in [(70, 2, 1793232000, None), (71, 1, None, 1793145600)]:
        detail = bytearray(1508)
        identity(detail, 0, 0xc9, key, 212, 7)
        put(detail, 1418, "I", 1)
        detail[1474] = flag
        detail[1491] = 3
        for at, seconds in [(294, due), (286, completed)]:
            put(detail, at, "Q", 0x2bca2875f4373fff if seconds is None
                else 621355968000000000 + seconds * 10000000)
        data += envelope(0x060f, detail)
    return data


def store(tag):
    data = payload(tag)
    extra = len(data) - 15
    compressed = bytes([0xf0]) + bytes([255]) * (extra // 255) + bytes([extra % 255]) + data
    block = bytearray(40)
    struct.pack_into("<QIIII", block, 8, 0x5d0245643b706a05, 8, len(compressed), len(data), 4)
    block.extend(compressed)
    put(block, 4, "I", zlib.crc32(block[8:]))
    put(block, 0, "I", zlib.crc32(block[4:32]))
    header = bytearray(64)
    header[:9] = b"Nostromoi"
    put(header, 56, "Q", 4096)
    return header + block


if __name__ == "__main__":
    for name, tag in [("folder-04c2", 0x04c2), ("folder-04d8", 0x04d8)]:
        (HERE / (name + ".hxd")).write_bytes(store(tag))
    bad = store(0x04d8)
    bad[-1] ^= 1
    (HERE / "corrupt-crc.hxd").write_bytes(bad)
    (HERE / "truncated-block.hxd").write_bytes(store(0x04d8)[:-32])
    (HERE / "truncated-header.hxd").write_bytes(b"Nostromoi")
