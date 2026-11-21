"""Generate the two neutral, repository-native fnOS package icon assets."""
from pathlib import Path
import struct
import zlib


def icon(size):
    def block(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            u, v = (x + 0.5) * 64 / size, (y + 0.5) * 64 / size
            bx, by = max(abs(u - 32) - 16, 0), max(abs(v - 32) - 16, 0)
            color = (24, 53, 77, 255) if bx * bx + by * by < 100 else (0, 0, 0, 0)
            if 12 <= u < 52 and 18 <= v < 45:
                color = (11, 32, 50, 255)
            if ((21 <= u < 24 and abs(v - (23 + u - 21)) < 1.5) or
                (24 <= u < 29 and abs(v - (26 + u - 24)) < 1.5) or
                (21 <= u < 29 and abs(v - (38 - u + 21)) < 1.5) or
                (33 <= u < 43 and 36 <= v < 39)):
                color = (141, 236, 206, 255)
            rows.extend(color)
    return (b"\x89PNG\r\n\x1a\n"
            + block(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + block(b"sRGB", b"\0") + block(b"IDAT", zlib.compress(rows, 9))
            + block(b"IEND", b""))


if __name__ == "__main__":
    dest = Path(__file__).resolve().parents[1] / "packages" / "cli"
    for name, side in [("ICON.PNG", 64), ("ICON_256.PNG", 256)]:
        (dest / name).write_bytes(icon(side))
