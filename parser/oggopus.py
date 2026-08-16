#!/usr/bin/env python3
"""
Minimal Ogg/Opus muxer.

CS2 stores voice as raw Opus packets with no container. Browsers cannot play
those directly, but every current browser plays Ogg/Opus natively - so rather
than take a decoder dependency (libopus bindings, or shelling out to ffmpeg)
we wrap the packets the demo already contains. Ogg framing is simple enough
that this is ~80 lines and no dependency at all.

Reference: RFC 3533 (Ogg) and RFC 7845 (Ogg Encapsulation for Opus).
"""

import struct

OPUS_RATE = 48000          # Opus always reports 48kHz in the container
PRE_SKIP = 312             # libopus default; trims the decoder's warm-up
MAX_SEGMENTS = 255         # per Ogg page


def _crc_table():
    """
    Ogg's CRC32 is NOT the common zlib one: polynomial 0x04c11db7, initial
    value 0, no input/output reflection and no final xor. Using zlib.crc32
    here produces pages that every player rejects.
    """
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


_CRC = _crc_table()


def _crc32(data):
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) & 0xFF) ^ b]
    return crc


def _lacing(packet_lengths):
    """Ogg segment table: each packet becomes N 255s plus a final remainder.

    A packet whose length is an exact multiple of 255 still needs the trailing
    zero, otherwise the decoder keeps reading into the next packet.
    """
    table = bytearray()
    for n in packet_lengths:
        while n >= 255:
            table.append(255)
            n -= 255
        table.append(n)
    return bytes(table)


def _page(body, *, serial, seq, granule, bos=False, eos=False, packet_lengths=None):
    header_type = (0x02 if bos else 0) | (0x04 if eos else 0)
    seg = _lacing(packet_lengths if packet_lengths is not None else [len(body)])
    if len(seg) > MAX_SEGMENTS:
        raise ValueError(f"{len(seg)} segments exceeds the {MAX_SEGMENTS} per page")
    head = (b"OggS" + bytes([0, header_type])
            + struct.pack("<q", granule)
            + struct.pack("<I", serial)
            + struct.pack("<I", seq)
            + b"\x00\x00\x00\x00"          # CRC placeholder
            + bytes([len(seg)]) + seg)
    page = head + body
    crc = _crc32(page)
    return page[:22] + struct.pack("<I", crc) + page[26:]


def _opus_head(channels=1):
    return (b"OpusHead"
            + bytes([1, channels])
            + struct.pack("<H", PRE_SKIP)
            + struct.pack("<I", OPUS_RATE)
            + struct.pack("<h", 0)          # output gain
            + bytes([0]))                   # channel mapping family


def _opus_tags(vendor=b"cs2coach"):
    return (b"OpusTags"
            + struct.pack("<I", len(vendor)) + vendor
            + struct.pack("<I", 0))         # no user comments


def mux(packets, samples_per_packet=480, channels=1, serial=1):
    """
    Wrap raw Opus packets into a complete Ogg/Opus stream.

    `samples_per_packet` is at 48kHz: CS2 voice is 10ms frames, so 480. The
    caller is responsible for only passing packets of one consistent duration -
    granule positions would drift otherwise, and the audio would slowly
    desynchronise from the demo timeline rather than fail outright.
    """
    out = bytearray()
    seq = 0
    out += _page(_opus_head(channels), serial=serial, seq=seq, granule=0, bos=True)
    seq += 1
    out += _page(_opus_tags(), serial=serial, seq=seq, granule=0)
    seq += 1

    granule = PRE_SKIP
    batch, lengths, segments = [], [], 0
    total = len(packets)

    def flush(is_last):
        nonlocal out, seq, batch, lengths, segments
        if not batch:
            return
        out += _page(b"".join(batch), serial=serial, seq=seq, granule=granule,
                     eos=is_last, packet_lengths=lengths)
        seq += 1
        batch, lengths, segments = [], [], 0

    for i, pkt in enumerate(packets):
        need = len(pkt) // 255 + 1
        if segments + need > MAX_SEGMENTS:
            flush(False)
        batch.append(pkt)
        lengths.append(len(pkt))
        segments += need
        granule += samples_per_packet
        if i == total - 1:
            flush(True)
    if batch:                                # only if the loop never flushed
        flush(True)
    return bytes(out)
