#!/usr/bin/env python3
"""

将程序所在目录下的文件夹内容，替换到 KPK 中同前缀下的全部条目。
流程: 扫描建索引 → 分析差异 → 二次确认 → 紧缩保留 → 追加补丁 → 截断

pip install lz4
"""

import os, sys, struct, zlib, lz4.block

# ==================== 配置区 ====================
KPK_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\DIG\res\base.kpk"

# 替换目录
PATCH_FOLDERS = ["data", "font"]

# ================================================

SIG        = b'KinoArchive\x01\x00'   # 13 字节文件头
SIG_LEN    = len(SIG)
HDR_LEN    = 23                        # 条目头固定部分
CHUNK_SIZE = 8 * 1024 * 1024           # 分块拷贝 8 MB


# ─── 工具函数 ─────────────────────────────────────

def fmt_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024 or unit == 'GB':
            return f"{n:.2f} {unit}" if unit != 'B' else f"{n} B"
        n /= 1024


def build_index(filepath):
    """
    扫描 KPK 文件，只读取每个条目的头部信息（不读文件体），
    返回 [{name, offset, total_size}, ...] 列表，按出现顺序排列。
    """
    entries = []
    with open(filepath, 'rb') as f:
        sig = f.read(SIG_LEN)
        if sig != SIG:
            raise ValueError(f"KPK 签名不匹配: {filepath}")
        while True:
            offset = f.tell()
            hdr = f.read(HDR_LEN)
            if len(hdr) < HDR_LEN:
                break
            name_len, comp_size, _decomp_size = struct.unpack('<HQQ', hdr[:18])
            # flags = hdr[18], crc = hdr[19:23] — 建索引时不需要
            name_bytes = f.read(name_len)
            if len(name_bytes) < name_len:
                break
            name = name_bytes.decode('utf-8')
            f.seek(comp_size, os.SEEK_CUR)          # 跳过文件体
            entries.append({
                'name':       name,
                'offset':     offset,                # 条目起始偏移
                'total_size': HDR_LEN + name_len + comp_size,
            })
    return entries


def collect_local_files(base_dir, folders):
    """
    收集 base_dir 下指定文件夹中的所有文件，
    返回 {KPK内相对路径(反斜杠): 磁盘绝对路径}。
    """
    result = {}
    for folder in folders:
        folder_path = os.path.join(base_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for root, _, filenames in os.walk(folder_path):
            for fn in filenames:
                full = os.path.join(root, fn)
                rel  = os.path.relpath(full, base_dir).replace('/', '\\')
                result[rel] = full
    return result


def copy_block(f, src, dst, size):
    """
    在同一个文件内，把 [src, src+size) 拷贝到 [dst, dst+size)。
    要求 dst <= src（前向拷贝，分块处理，重叠安全）。
    """
    if src == dst or size == 0:
        return
    copied = 0
    while copied < size:
        n = min(CHUNK_SIZE, size - copied)
        f.seek(src + copied)
        data = f.read(n)
        f.seek(dst + copied)
        f.write(data)
        copied += n


def write_new_entry(f, name, filepath):
    """
    读取磁盘文件，压缩（若有益），写入一个完整的 KPK 条目到 f 当前位置。
    返回写入的字节数。
    """
    raw      = open(filepath, 'rb').read()
    raw_size = len(raw)
    crc      = zlib.crc32(raw) & 0xFFFFFFFF
    nb       = name.encode('utf-8')

    if name.lower().endswith('.mp4'):
        flags, body = 0x03, raw                     # mp4 原样存储
    else:
        comp = lz4.block.compress(raw, store_size=False)
        if len(comp) < raw_size:
            flags, body = 0x00, comp                # LZ4 压缩
        else:
            flags, body = 0x01, raw                 # 压缩无增益，原样存储

    f.write(struct.pack('<HQQ', len(nb), len(body), raw_size))
    f.write(struct.pack('<BI', flags, crc))
    f.write(nb)
    f.write(body)

    return HDR_LEN + len(nb) + len(body)


# ─── 主流程 ───────────────────────────────────────

def patch():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isfile(KPK_PATH):
        print(f"错误: 找不到 KPK 文件: {KPK_PATH}")
        sys.exit(1)

    original_size = os.path.getsize(KPK_PATH)

    # ── 1. 建索引 ──
    print(f"[1/5] 扫描 KPK 建立索引 ({fmt_size(original_size)}) ...")
    entries = build_index(KPK_PATH)
    print(f"      共 {len(entries)} 个条目")

    # ── 2. 收集本地补丁文件 ──
    print(f"[2/5] 收集本地补丁文件 ...")
    local_files = collect_local_files(script_dir, PATCH_FOLDERS)

    # 仅处理本地实际存在的文件夹对应的前缀
    affected_prefixes = set()
    for folder in PATCH_FOLDERS:
        if os.path.isdir(os.path.join(script_dir, folder)):
            affected_prefixes.add(folder + '\\')

    if not affected_prefixes:
        print("      未找到任何本地补丁文件夹，退出。")
        return

    print(f"      本地 {len(local_files)} 个文件, 涉及前缀: "
          f"{', '.join(sorted(affected_prefixes))}")

    # ── 3. 分析差异 ──
    print(f"[3/5] 分析差异 ...")

    def is_affected(name):
        return any(name.startswith(p) for p in affected_prefixes)

    old_names = {e['name'] for e in entries if is_affected(e['name'])}
    new_names = set(local_files.keys())

    modified = sorted(old_names & new_names)
    deleted  = sorted(old_names - new_names)
    added    = sorted(new_names - old_names)
    unchanged_count = sum(1 for e in entries if not is_affected(e['name']))

    # ── 4. 展示差异 ──
    print()
    print('=' * 62)
    print(f"  KPK 文件:         {KPK_PATH}")
    print(f"  原始大小:         {fmt_size(original_size)}")
    print(f"  总条目数:         {len(entries)}")
    print(f"  不受影响 (保留):  {unchanged_count}")
    print(f"  ────────────────────────────────────────")
    print(f"  覆盖 (修改):      {len(modified)}")
    print(f"  移除 (删除):      {len(deleted)}")
    print(f"  追加 (新增):      {len(added)}")
    print('=' * 62)

    def show_list(title, items, limit=15):
        if not items:
            return
        print(f"\n  【{title}】")
        for x in items[:limit]:
            print(f"    {x}")
        if len(items) > limit:
            print(f"    ... 还有 {len(items) - limit} 个")

    show_list("修改", modified)
    show_list("删除", deleted)
    show_list("新增", added)

    if not (modified or deleted or added):
        print("\n无任何变更，退出。")
        return

    # ── 5. 二次确认 ──
    print()
    print("⚠  警告: 此操作将直接修改原 KPK 文件!")
    print("   如中途中断（断电/强制终止），文件可能损坏。")
    print("   建议操作前自行备份。")
    print()
    ans = input("确认执行? 请输入 yes: ").strip().lower()
    if ans != 'yes':
        print("已取消。")
        return

    # ══════════════════════════════════════════════
    # Phase A: 紧缩保留条目（填补被删条目留下的空隙）
    # ──────────────────────────────────────────────
    # 原理: 按原顺序遍历全部条目，跳过被影响的条目，
    # 把保留条目依次前移。因为 write_pos ≤ entry.offset
    # 恒成立（只跳过从未增加），所以前向拷贝安全无重叠问题。
    # ══════════════════════════════════════════════
    print(f"\n[4/5] 紧缩保留条目 ...")

    with open(KPK_PATH, 'r+b') as f:
        write_pos   = SIG_LEN
        kept        = 0
        bytes_moved = 0

        for entry in entries:
            if is_affected(entry['name']):
                continue                           # 跳过 = 删除
            src = entry['offset']
            sz  = entry['total_size']
            if write_pos < src:
                copy_block(f, src, write_pos, sz)
                bytes_moved += sz
            write_pos += sz
            kept += 1
            if kept % 2000 == 0:
                print(f"      已处理 {kept}/{unchanged_count} 保留条目 "
                      f"(数据移动 {fmt_size(bytes_moved)}) ...", flush=True)

        print(f"      保留 {kept} 个条目, 数据移动 {fmt_size(bytes_moved)}")

        # ══════════════════════════════════════════
        # Phase B: 追加新/修改条目
        # ══════════════════════════════════════════
        total_new = len(local_files)
        print(f"[5/5] 写入 {total_new} 个补丁条目 ...")

        f.seek(write_pos)
        for i, (name, path) in enumerate(sorted(local_files.items())):
            write_pos += write_new_entry(f, name, path)
            if (i + 1) % 500 == 0:
                print(f"      {i+1}/{total_new} ...", flush=True)

        # ══════════════════════════════════════════
        # Phase C: 截断多余尾部
        # ══════════════════════════════════════════
        f.flush()
        f.truncate(write_pos)

    # ── 完成报告 ──
    new_size = write_pos
    diff     = new_size - original_size
    sign     = '+' if diff >= 0 else ''

    print()
    print('=' * 62)
    print(f"  ✔ 替换完成!")
    print(f"  原始大小: {fmt_size(original_size)}")
    print(f"  新的大小: {fmt_size(new_size)}")
    print(f"  大小变化: {sign}{fmt_size(abs(diff))}")
    print(f"  条目总数: {kept + total_new}")
    print('=' * 62)


if __name__ == '__main__':
    patch()