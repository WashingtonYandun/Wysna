/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_OPERATORS_H
#define WYSNA_RUNTIME_OPERATORS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Binary Operations */
static inline WVal wysna_add(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_int((int64_t)((uint64_t)wysna_as_int(a) + (uint64_t)wysna_as_int(b)));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_float(wysna_as_float(a) + wysna_as_float(b));
    if (ta == W_STR && tb == W_STR) {
        char sba[7], sbb[7];
        size_t la = 0, lb = 0;
        const char* da = wysna_str_bytes(a, sba, &la);
        const char* db = wysna_str_bytes(b, sbb, &lb);
        if (la == 0) return wysna_clone(b);
        if (lb == 0) return wysna_clone(a);
        if (la + lb <= 6) {
            char small_buf[7];
            memcpy(small_buf, da, la);
            memcpy(small_buf + la, db, lb);
            return wysna_sso_from_n(small_buf, la + lb);
        }
        char* res = (char*)wysna_alloc_safe(la + lb + 1);
        memcpy(res, da, la);
        memcpy(res + la, db, lb);
        res[la + lb] = '\0';
        return wysna_owned_str_n(res, la + lb);
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for addition (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_sub(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_int((int64_t)((uint64_t)wysna_as_int(a) - (uint64_t)wysna_as_int(b)));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_float(wysna_as_float(a) - wysna_as_float(b));
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for subtraction (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_mul(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_int((int64_t)((uint64_t)wysna_as_int(a) * (uint64_t)wysna_as_int(b)));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_float(wysna_as_float(a) * wysna_as_float(b));
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for multiplication (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline int64_t wysna_div_i64_at(int64_t ai, int64_t bi, const char* file, int32_t line, int32_t col) {
    if (bi == 0) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4001", "Division by zero");
    }
    if (ai == INT64_MIN && bi == -1) return INT64_MIN;
    return ai / bi;
}

static inline double wysna_div_f64_at(double af, double bf, const char* file, int32_t line, int32_t col) {
    if (bf == 0.0) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4001", "Division by zero");
    }
    return af / bf;
}

static inline int64_t wysna_mod_i64_at(int64_t ai, int64_t bi, const char* file, int32_t line, int32_t col) {
    if (bi == 0) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4001", "Modulo by zero");
    }
    if (ai == INT64_MIN && bi == -1) return 0;
    return ai % bi;
}

static inline WVal wysna_div(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) {
        int64_t ai = wysna_as_int(a);
        int64_t bi = wysna_as_int(b);
        if (bi == 0) wysna_runtime_fail("W4001", "Division by zero");
        if (ai == INT64_MIN && bi == -1) return wysna_int(INT64_MIN);
        return wysna_int(ai / bi);
    }
    if (ta == W_FLOAT && tb == W_FLOAT) {
        double af = wysna_as_float(a);
        double bf = wysna_as_float(b);
        if (bf == 0.0) wysna_runtime_fail("W4001", "Division by zero");
        return wysna_float(af / bf);
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for division (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_div_at(WVal a, WVal b, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_div(a, b);
}

static inline WVal wysna_mod(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) {
        int64_t ai = wysna_as_int(a);
        int64_t bi = wysna_as_int(b);
        if (bi == 0) wysna_runtime_fail("W4001", "Modulo by zero");
        if (ai == INT64_MIN && bi == -1) return wysna_int(0);
        return wysna_int(ai % bi);
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for modulo (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_mod_at(WVal a, WVal b, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_mod(a, b);
}

static inline WVal wysna_neg(WVal a) {
    WTag ta = wysna_tag(a);
    if (ta == W_INT) return wysna_int((int64_t)(0ULL - (uint64_t)wysna_as_int(a)));
    if (ta == W_FLOAT) return wysna_float(-wysna_as_float(a));
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand type for negation (tag %d)\n", ta);
    exit(1);
}

static inline int wysna_string_compare(WVal a, WVal b) {
    if (a.bits == b.bits) return 0;
    char sba[7], sbb[7];
    size_t la = 0, lb = 0;
    const char* da = wysna_str_bytes(a, sba, &la);
    const char* db = wysna_str_bytes(b, sbb, &lb);
    if (da == db && la == lb) return 0;
    size_t min_len = la < lb ? la : lb;
    int cmp = memcmp(da, db, min_len);
    if (cmp != 0) return cmp;
    return (la < lb) ? -1 : ((la > lb) ? 1 : 0);
}

static inline WVal wysna_lt(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_bool(wysna_as_int(a) < wysna_as_int(b));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_bool(wysna_as_float(a) < wysna_as_float(b));
    if (ta == W_STR && tb == W_STR) return wysna_bool(wysna_string_compare(a, b) < 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_le(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_bool(wysna_as_int(a) <= wysna_as_int(b));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_bool(wysna_as_float(a) <= wysna_as_float(b));
    if (ta == W_STR && tb == W_STR) return wysna_bool(wysna_string_compare(a, b) <= 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_gt(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_bool(wysna_as_int(a) > wysna_as_int(b));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_bool(wysna_as_float(a) > wysna_as_float(b));
    if (ta == W_STR && tb == W_STR) return wysna_bool(wysna_string_compare(a, b) > 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", ta, tb);
    exit(1);
}

static inline WVal wysna_ge(WVal a, WVal b) {
    WTag ta = wysna_tag(a);
    WTag tb = wysna_tag(b);
    if (ta == W_INT && tb == W_INT) return wysna_bool(wysna_as_int(a) >= wysna_as_int(b));
    if (ta == W_FLOAT && tb == W_FLOAT) return wysna_bool(wysna_as_float(a) >= wysna_as_float(b));
    if (ta == W_STR && tb == W_STR) return wysna_bool(wysna_string_compare(a, b) >= 0);
    fprintf(stderr, "[W4003] RuntimeError: Invalid operand types for comparison (tags %d, %d)\n", ta, tb);
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
