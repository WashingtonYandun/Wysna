"""LLVM string quoting independent of code-generation state."""
def quote_bytes(data):
    return '"' + ''.join(chr(byte) if 32 <= byte < 127 and byte not in (34, 92)
                         else '\\' + format(byte, '02X') for byte in data) + '"'
