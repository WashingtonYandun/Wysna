iota_count = 0


def iota(restart: bool = False) -> int:
    global iota_count

    if restart:
        iota_count = 0

    counter = iota_count
    iota_count = iota_count + 1
    return counter


def is_skippable(char: str) -> bool:
    return char == " " or char == "\t"


def is_int(char: str) -> bool:
    c = ord(char)
    bounds = [ord('0'), ord('9')]
    return bounds[0] <= c <= bounds[1]


def is_alpha(src: str) -> bool:
    return src.upper() != src.lower()
