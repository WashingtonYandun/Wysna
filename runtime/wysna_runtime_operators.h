/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_OPERATORS_H
#define WYSNA_RUNTIME_OPERATORS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Binary Operations */
static inline WVal wysna_add(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_int((int64_t)((uint64_t)a.as.i + (uint64_t)b.as.i));
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_float(a.as.f + b.as.f);
    if (a.tag == W_STR && b.tag == W_STR) {
        size_t la = a.as.string.length;
        size_t lb = b.as.string.length;
        char* res = (char*)wysna_alloc_safe(la + lb + 1);
        memcpy(res, a.as.string.data, la);
        memcpy(res + la, b.as.string.data, lb);
        res[la + lb] = '\0';
        return wysna_owned_str_n(res, la + lb);
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for addition (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_sub(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_int((int64_t)((uint64_t)a.as.i - (uint64_t)b.as.i));
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_float(a.as.f - b.as.f);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for subtraction (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_mul(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_int((int64_t)((uint64_t)a.as.i * (uint64_t)b.as.i));
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_float(a.as.f * b.as.f);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for multiplication (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_div(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) {
        if (b.as.i == 0) wysna_runtime_fail("W4001", "Division by zero");
        if (a.as.i == INT64_MIN && b.as.i == -1) return wysna_int(INT64_MIN);
        return wysna_int(a.as.i / b.as.i);
    }
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) {
        if (b.as.f == 0.0) wysna_runtime_fail("W4001", "Division by zero");
        return wysna_float(a.as.f / b.as.f);
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for division (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_div_at(WVal a, WVal b, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_div(a, b);
}

static inline WVal wysna_mod(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) {
        if (b.as.i == 0) wysna_runtime_fail("W4001", "Modulo by zero");
        if (a.as.i == INT64_MIN && b.as.i == -1) return wysna_int(0);
        return wysna_int(a.as.i % b.as.i);
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for modulo (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_mod_at(WVal a, WVal b, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_mod(a, b);
}

static inline WVal wysna_neg(WVal a) {
    if (a.tag == W_INT) return wysna_int((int64_t)(0ULL - (uint64_t)a.as.i));
    if (a.tag == W_FLOAT) return wysna_float(-a.as.f);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand type for negation (tag %d)\n", a.tag);
    exit(1);
}

static inline WVal wysna_lt(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_bool(a.as.i < b.as.i);
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_bool(a.as.f < b.as.f);
    if (a.tag == W_STR && b.tag == W_STR) return wysna_bool(strcmp(a.as.string.data, b.as.string.data) < 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_le(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_bool(a.as.i <= b.as.i);
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_bool(a.as.f <= b.as.f);
    if (a.tag == W_STR && b.tag == W_STR) return wysna_bool(strcmp(a.as.string.data, b.as.string.data) <= 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_gt(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_bool(a.as.i > b.as.i);
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_bool(a.as.f > b.as.f);
    if (a.tag == W_STR && b.tag == W_STR) return wysna_bool(strcmp(a.as.string.data, b.as.string.data) > 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

static inline WVal wysna_ge(WVal a, WVal b) {
    if (a.tag == W_INT && b.tag == W_INT) return wysna_bool(a.as.i >= b.as.i);
    if (a.tag == W_FLOAT && b.tag == W_FLOAT) return wysna_bool(a.as.f >= b.as.f);
    if (a.tag == W_STR && b.tag == W_STR) return wysna_bool(strcmp(a.as.string.data, b.as.string.data) >= 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", a.tag, b.tag);
    exit(1);
}

/*
 * Shortest round-trip formatting for binary64, matching Python's `repr(float)`.
 *
 * The previous implementation used "%g" (six significant digits) plus a ".0"
 * heuristic, so `0.1 + 0.2` printed `0.3` and `1.0 / 3.0` printed `0.333333` in
 * compiled programs while the interpreter printed `0.30000000000000004` and
 * `0.3333333333333333`. Two engines disagreeing on how they print a float is a
 * language-level bug, not a cosmetic one.
 *
 * The digits are found by trying precisions 1..17 and keeping the first whose
 * value reads back as the same double; 17 significant digits always suffice.
 * Python's presentation rule is then applied: fixed notation while the decimal
 * exponent is in [-4, 15], scientific notation outside it, and a ".0" suffix for
 * an integral value so that floats stay distinguishable from ints. Infinities and
 * NaN print as `inf`, `-inf` and `nan`.
 */
static inline char* wysna_format_float(double value) {
    char* buf = (char*)wysna_alloc_safe(40);
    if (value != value) { strcpy(buf, "nan"); return buf; }
    if (value > DBL_MAX) { strcpy(buf, "inf"); return buf; }
    if (value < -DBL_MAX) { strcpy(buf, "-inf"); return buf; }
    if (value == 0.0) {
        strcpy(buf, signbit(value) ? "-0.0" : "0.0");
        return buf;
    }

    char digits[32];
    int exponent = 0;
    int length = 0;
    for (int precision = 1; precision <= 17; precision++) {
        char candidate[48];
        snprintf(candidate, sizeof(candidate), "%.*e", precision - 1, value);
        if (strtod(candidate, NULL) == value) {
            const char* mantissa = candidate;
            bool negative = (*mantissa == '-');
            if (negative) mantissa++;
            int written = 0;
            for (const char* p = mantissa; *p && *p != 'e' && *p != 'E'; p++) {
                if (*p == '.') continue;
                if (written < (int)sizeof(digits) - 1) digits[written++] = *p;
            }
            digits[written] = 0;
            const char* e = strchr(mantissa, 'e');
            exponent = e ? (int)strtol(e + 1, NULL, 10) : 0;
            length = written;
            while (length > 1 && digits[length - 1] == '0') length--;
            if (negative) {
                /* Re-attach the sign by shifting the digit buffer. */
                memmove(digits + 1, digits, (size_t)length);
                digits[0] = '-';
                length++;
            }
            break;
        }
    }
    if (length == 0) {
        /* Unreachable: 17 significant digits always round-trip. */
        snprintf(buf, 40, "%.17g", value);
        if (!strchr(buf, '.') && !strchr(buf, 'e')) strcat(buf, ".0");
        return buf;
    }

    bool negative = digits[0] == '-';
    const char* body = negative ? digits + 1 : digits;
    int digit_count = negative ? length - 1 : length;

    if (exponent < -4 || exponent >= 16) {
        char* out = buf;
        if (negative) *out++ = '-';
        *out++ = body[0];
        /* Python does not pad the mantissa: `1e-05`, not `1.0e-05`. */
        if (digit_count > 1) {
            *out++ = '.';
            memcpy(out, body + 1, (size_t)(digit_count - 1));
            out += digit_count - 1;
        }
        *out++ = 'e';
        int written = snprintf(out, 8, "%+03d", exponent);
        (void)written;
        return buf;
    }

    if (exponent >= digit_count - 1) {
        /* Integral value: digits then trailing zeros, then ".0". */
        char* out = buf;
        if (negative) *out++ = '-';
        memcpy(out, body, (size_t)digit_count);
        out += digit_count;
        for (int i = 0; i < exponent - (digit_count - 1); i++) *out++ = '0';
        *out++ = '.';
        *out++ = '0';
        *out = 0;
        return buf;
    }

    if (exponent >= 0) {
        char* out = buf;
        if (negative) *out++ = '-';
        memcpy(out, body, (size_t)(exponent + 1));
        out += exponent + 1;
        *out++ = '.';
        memcpy(out, body + exponent + 1, (size_t)(digit_count - exponent - 1));
        out += digit_count - exponent - 1;
        *out = 0;
        return buf;
    }

    {
        char* out = buf;
        if (negative) *out++ = '-';
        *out++ = '0';
        *out++ = '.';
        for (int i = 0; i < -exponent - 1; i++) *out++ = '0';
        memcpy(out, body, (size_t)digit_count);
        out += digit_count;
        *out = 0;
        return buf;
    }
}

#endif /* WYSNA_RUNTIME_OPERATORS_H */
