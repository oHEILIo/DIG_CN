#!/usr/bin/env python3
"""
base.kpk 解压/压缩工具
pip install lz4
"""
import os, struct, zlib, lz4.block

# ==================== 配置区 ====================
MODE = 0  # 0=解压, 1=压缩

# 解压配置
EXTRACT_DIR = r"extracted"
KPK_PATH    = r"C:\Program Files (x86)\Steam\steamapps\common\DIG\res\base.kpk"

# 压缩配置
SOURCE_DIR  = r"extracted"
OUTPUT_KPK  = r"base_mod.kpk"
PACK_FOLDERS = ["data", "font", "image", "movie", "script", "shader", "sound"]
# ================================================

#  文件头: KinoArchive(11) + 版本0x01(1) + 0x00(1) = 13字节
#  条目头: name_len(u16) + comp_size(u64) + decomp_size(u64) + flags(u8) + crc32(u32) = 23字节
#  flags:  0x00=LZ4压缩  其余=原样存储(0x01常规/0x03=mp4)
#  crc32:  校验对象为解压后数据

SIG = b'KinoArchive\x01\x00'

def extract():
    with open(KPK_PATH, 'rb') as f:
        assert f.read(13) == SIG, "签名不匹配"
        n = 0
        while True:
            hdr = f.read(23)
            if len(hdr) < 23:
                break
            nl, cs, ds = struct.unpack('<HQQ', hdr[:18])
            flags = hdr[18]
            name = f.read(nl).decode('utf-8')
            body = f.read(cs)
            data = lz4.block.decompress(body, uncompressed_size=ds) if flags == 0 else body
            path = os.path.join(EXTRACT_DIR, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'wb').write(data)
            n += 1
            if n % 500 == 0:
                print(f"  已解压 {n} ...", flush=True)
    print(f"完成: {n} 个文件 → {EXTRACT_DIR}")

def pack():
    files = []
    for folder in PACK_FOLDERS:
        base = os.path.join(SOURCE_DIR, folder)
        if not os.path.isdir(base):
            continue
        for root, _, names in os.walk(base):
            for fname in names:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, SOURCE_DIR).replace('/', '\\')
                files.append((rel, full))
    files.sort()

    with open(OUTPUT_KPK, 'wb') as f:
        f.write(SIG)
        for i, (name, path) in enumerate(files):
            raw = open(path, 'rb').read()
            crc = zlib.crc32(raw) & 0xFFFFFFFF
            nb = name.encode('utf-8')

            if name.lower().endswith('.mp4'):
                flags, body = 0x03, raw
            else:
                comp = lz4.block.compress(raw, store_size=False)
                if len(comp) < len(raw):
                    flags, body = 0x00, comp
                else:
                    flags, body = 0x01, raw

            f.write(struct.pack('<HQQ', len(nb), len(body), len(raw)))
            f.write(struct.pack('<BI', flags, crc))
            f.write(nb)
            f.write(body)
            if (i + 1) % 500 == 0:
                print(f"  已打包 {i+1}/{len(files)} ...", flush=True)
    print(f"完成: {len(files)} 个文件 → {OUTPUT_KPK}")

if __name__ == '__main__':
    [extract, pack][MODE]()