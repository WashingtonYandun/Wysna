/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_VALUES_H
#define WYSNA_RUNTIME_VALUES_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

static inline WVal wysna_make_hi(uint64_t hi, uint64_t payload) {
    WVal v;
    v.bits = (hi << WVAL_HI_SHIFT) | (payload & WVAL_PAYLOAD_MASK);
    return v;
}

static inline WVal wysna_make_hi_ptr(uint64_t hi, const void* ptr) {
    uint64_t addr = (uint64_t)(uintptr_t)ptr;
    if ((addr >> WVAL_HI_SHIFT) != 0) {
        wysna_runtime_fail("W4003", "Pointer exceeds 48-bit tagged range");
    }
    return wysna_make_hi(hi, addr);
}

static inline uint32_t wysna_hi(WVal v) {
    return (uint32_t)(v.bits >> WVAL_HI_SHIFT);
}

static inline uint64_t wysna_payload(WVal v) {
    return v.bits & WVAL_PAYLOAD_MASK;
}

static inline bool wysna_is_double(WVal v) {
    return (v.bits - WVAL_DOUBLE_OFFSET) <= WVAL_DOUBLE_MAX_ENC;
}

/* Immediates (< WVAL_HI_STR_PTR) need no ARC retain/release or pointer chase. */
static inline bool wysna_is_immediate(WVal v) {
    return v.bits < (WVAL_HI_STR_PTR << WVAL_HI_SHIFT);
}

static inline WTag wysna_tag(WVal v) {
    if (wysna_is_double(v)) return W_FLOAT;
    if (v.bits <= WVAL_TRUE_BITS) {
        static const uint8_t low_tags[4] = { W_UNIT, W_NONE, W_BOOL, W_BOOL };
        return (WTag)low_tags[v.bits];
    }
    uint32_t hi = wysna_hi(v);
    if (hi == 0) return W_POINTER;
    switch (hi) {
        case WVAL_HI_INT:       return W_INT;
        case WVAL_HI_SSO_SHORT:
        case WVAL_HI_SSO_6:
        case WVAL_HI_CSTR:
        case WVAL_HI_STR_PTR:   return W_STR;
        case WVAL_HI_LIST:      return W_LIST;
        case WVAL_HI_DICT:      return W_DICT;
        case WVAL_HI_TUPLE:     return W_TUPLE;
        case WVAL_HI_STRUCT:    return W_STRUCT;
        case WVAL_HI_OBJECT:    return W_OBJECT;
        case WVAL_HI_ENUM:      return W_ENUM;
        case WVAL_HI_OK:        return W_OK;
        case WVAL_HI_ERR:       return W_ERR;
        case WVAL_HI_EXT: {
            const uint32_t* kind = (const uint32_t*)(uintptr_t)wysna_payload(v);
            return kind ? (WTag)(*kind) : W_POINTER;
        }
        default:                return W_UNIT;
    }
}

/* Constructors */
static inline WVal wysna_unit(void) {
    WVal v; v.bits = WVAL_UNIT_BITS; return v;
}

static inline WVal wysna_none(void) {
    WVal v; v.bits = WVAL_NONE_BITS; return v;
}

static inline WVal wysna_bool(bool x) {
    WVal v; v.bits = x ? WVAL_TRUE_BITS : WVAL_FALSE_BITS; return v;
}

static inline bool wysna_int_fits_48(int64_t x) {
    return x >= -(INT64_C(1) << 47) && x <= ((INT64_C(1) << 47) - 1);
}

static inline WVal wysna_int_inline(int64_t x) {
    return wysna_make_hi(WVAL_HI_INT, (uint64_t)x);
}

static inline WVal wysna_int(int64_t x) {
    if (wysna_int_fits_48(x)) return wysna_int_inline(x);
    WIntBox* box = (WIntBox*)wysna_alloc_safe(sizeof(WIntBox));
    box->kind = W_INT;
    box->refcount = 1;
    box->value = x;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_EXT, box);
}

static inline WVal wysna_int_borrow_finish(WIntBox* stack_box, int64_t x) {
    if (wysna_int_fits_48(x)) return wysna_int_inline(x);
    stack_box->kind = W_INT;
    stack_box->refcount = -2;
    stack_box->value = x;
    return wysna_make_hi_ptr(WVAL_HI_EXT, stack_box);
}
#define wysna_borrow_int(x) wysna_int_borrow_finish(&(WIntBox){0}, (x))

static inline WVal wysna_float(double x) {
    WVal v;
    if (isnan(x)) {
        v.bits = WVAL_CANON_NAN_ENC;
        return v;
    }
    uint64_t raw = 0;
    memcpy(&raw, &x, sizeof(raw));
    v.bits = raw + WVAL_DOUBLE_OFFSET;
    return v;
}

static inline WVal wysna_pointer(void* pointer) {
    uint64_t addr = (uint64_t)(uintptr_t)pointer;
    if ((addr & ~WVAL_RAW_PTR_MASK) != 0) {
        wysna_runtime_fail("W4003", "Opaque pointer exceeds 47-bit tagged space");
    }
    WVal v;
    v.bits = WVAL_RAW_PTR_FLAG | addr;
    return v;
}

/* Readers / Extractors */
static inline int64_t wysna_as_int(WVal v) {
    uint32_t hi = wysna_hi(v);
    if (hi == WVAL_HI_INT) {
        uint64_t raw = wysna_payload(v);
        return (int64_t)((raw ^ UINT64_C(0x0000800000000000)) - UINT64_C(0x0000800000000000));
    }
    if (hi == WVAL_HI_EXT) {
        const WIntBox* box = (const WIntBox*)(uintptr_t)wysna_payload(v);
        if (box && box->kind == W_INT) return box->value;
    }
    return 0;
}

static inline double wysna_as_float(WVal v) {
    if (!wysna_is_double(v)) return 0.0;
    uint64_t raw = v.bits - WVAL_DOUBLE_OFFSET;
    double d = 0.0;
    memcpy(&d, &raw, sizeof(d));
    return d;
}

static inline bool wysna_as_bool(WVal v) {
    return v.bits == WVAL_TRUE_BITS;
}

static inline WListBuf* wysna_as_list(WVal v) {
    return wysna_hi(v) == WVAL_HI_LIST ? (WListBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WDictBuf* wysna_as_dict(WVal v) {
    return wysna_hi(v) == WVAL_HI_DICT ? (WDictBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WTupleBuf* wysna_as_tuple(WVal v) {
    return wysna_hi(v) == WVAL_HI_TUPLE ? (WTupleBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WStructBuf* wysna_as_struct(WVal v) {
    uint32_t hi = wysna_hi(v);
    return (hi == WVAL_HI_STRUCT || hi == WVAL_HI_OBJECT) ? (WStructBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WEnumBuf* wysna_as_enum(WVal v) {
    return wysna_hi(v) == WVAL_HI_ENUM ? (WEnumBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WBoxBuf* wysna_as_box(WVal v) {
    uint32_t hi = wysna_hi(v);
    return (hi == WVAL_HI_OK || hi == WVAL_HI_ERR) ? (WBoxBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WWeakControl* wysna_as_weak(WVal v) {
    if (wysna_hi(v) == WVAL_HI_EXT) {
        WWeakControl* ctrl = (WWeakControl*)(uintptr_t)wysna_payload(v);
        if (ctrl && ctrl->kind == W_WEAK) return ctrl;
    }
    return NULL;
}

static inline WFunctionBuf* wysna_as_function(WVal v) {
    if (wysna_hi(v) == WVAL_HI_EXT) {
        WFunctionBuf* fn = (WFunctionBuf*)(uintptr_t)wysna_payload(v);
        if (fn && fn->kind == W_FUNCTION) return fn;
    }
    return NULL;
}

static inline WPointerOwner* wysna_pointer_owner(WVal v) {
    if (wysna_hi(v) == WVAL_HI_EXT) {
        WPointerOwner* owner = (WPointerOwner*)(uintptr_t)wysna_payload(v);
        if (owner && owner->kind == W_POINTER) return owner;
    }
    return NULL;
}

static inline void* wysna_as_pointer(WVal v) {
    if (wysna_hi(v) == 0 && (v.bits & WVAL_RAW_PTR_FLAG)) {
        return (void*)(uintptr_t)(v.bits & WVAL_RAW_PTR_MASK);
    }
    WPointerOwner* owner = wysna_pointer_owner(v);
    return owner ? owner->address : NULL;
}

static inline void wysna_set_vtable(WVal obj, const WClassVTable* table) {
    if (wysna_hi(obj) == WVAL_HI_OBJECT) {
        WStructBuf* st = (WStructBuf*)(uintptr_t)wysna_payload(obj);
        if (st) st->vtable = table;
    }
}

static inline bool wysna_is_tuple_len(WVal v, int32_t expected_len) {
    WTupleBuf* t = wysna_as_tuple(v);
    return t != NULL && t->len == expected_len;
}

static inline bool wysna_is_err(WVal v) {
    return wysna_hi(v) == WVAL_HI_ERR;
}

static inline bool wysna_is_ok(WVal v) {
    return wysna_hi(v) == WVAL_HI_OK;
}

static inline bool wysna_value_is_error_type(WVal value, const char* kind, const char* name) {
    WTag tag = wysna_tag(value);
    if (strcmp(kind, "int") == 0) return tag == W_INT;
    if (strcmp(kind, "float") == 0) return tag == W_FLOAT;
    if (strcmp(kind, "bool") == 0) return tag == W_BOOL;
    if (strcmp(kind, "str") == 0) return tag == W_STR;
    if (strcmp(kind, "struct") == 0) {
        WStructBuf* st = wysna_hi(value) == WVAL_HI_STRUCT ? wysna_as_struct(value) : NULL;
        return st && (st->name == name || strcmp(st->name, name) == 0);
    }
    if (strcmp(kind, "class") == 0) {
        WStructBuf* st = wysna_hi(value) == WVAL_HI_OBJECT ? wysna_as_struct(value) : NULL;
        return st && (st->name == name || strcmp(st->name, name) == 0);
    }
    if (strcmp(kind, "enum") == 0) {
        WEnumBuf* en = wysna_as_enum(value);
        return en && en->enum_name && (en->enum_name == name || strcmp(en->enum_name, name) == 0);
    }
    return false;
}

static inline WVal wysna_virtual_call(WVal receiver, int32_t slot, int32_t argc, const WVal* argv) {
    WStructBuf* st = wysna_hi(receiver) == WVAL_HI_OBJECT ? wysna_as_struct(receiver) : NULL;
    const WClassVTable* table = st ? st->vtable : NULL;
    if (!table || slot < 0 || slot >= table->count || !table->methods[slot]) {
        fprintf(stderr, "[W4003] RuntimeError: Invalid virtual method dispatch\n"); wysna_panic_exit(1);
    }
    return table->methods[slot](receiver, argc, argv);
}

static inline int32_t wysna_ffi_int32(WVal value) {
    if (wysna_tag(value) != W_INT) {
        fprintf(stderr, "[W4003] RuntimeError: int32 FFI argument out of range\n"); wysna_panic_exit(1);
    }
    int64_t i = wysna_as_int(value);
    if (i < INT32_MIN || i > INT32_MAX) {
        fprintf(stderr, "[W4003] RuntimeError: int32 FFI argument out of range\n"); wysna_panic_exit(1);
    }
    return (int32_t)i;
}

/* Inline 0..6-byte Small String Optimization (SSO) & WStringBuf / CSTR Strings */
static inline WVal wysna_sso_from_n(const char* s, size_t len) {
    if (len <= 5) {
        uint64_t payload = ((uint64_t)len) << 40;
        for (size_t i = 0; i < len; i++) {
            payload |= ((uint64_t)(unsigned char)s[i]) << (8U * i);
        }
        return wysna_make_hi(WVAL_HI_SSO_SHORT, payload);
    }
    uint64_t payload = 0;
    for (size_t i = 0; i < 6; i++) {
        payload |= ((uint64_t)(unsigned char)s[i]) << (8U * i);
    }
    return wysna_make_hi(WVAL_HI_SSO_6, payload);
}

static inline bool wysna_read_sso(WVal v, char out[7], size_t* out_len) {
    uint32_t hi = wysna_hi(v);
    if (hi == WVAL_HI_SSO_SHORT) {
        size_t len = (size_t)((v.bits >> 40) & UINT64_C(0xFF));
        if (len > 5) return false;
        for (size_t i = 0; i < len; i++) {
            out[i] = (char)((v.bits >> (8U * i)) & UINT64_C(0xFF));
        }
        out[len] = '\0';
        if (out_len) *out_len = len;
        return true;
    }
    if (hi == WVAL_HI_SSO_6) {
        for (size_t i = 0; i < 6; i++) {
            out[i] = (char)((v.bits >> (8U * i)) & UINT64_C(0xFF));
        }
        out[6] = '\0';
        if (out_len) *out_len = 6;
        return true;
    }
    return false;
}

static inline WStringBuf* wysna_str_owner(WVal v) {
    return wysna_hi(v) == WVAL_HI_STR_PTR ? (WStringBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline size_t wysna_str_len(WVal v) {
    switch (wysna_hi(v)) {
        case WVAL_HI_SSO_SHORT: return (size_t)((v.bits >> 40) & UINT64_C(0xFF));
        case WVAL_HI_SSO_6:     return 6;
        case WVAL_HI_STR_PTR: {
            const WStringBuf* b = (const WStringBuf*)(uintptr_t)wysna_payload(v);
            return b ? b->byte_length : 0;
        }
        case WVAL_HI_CSTR: {
            const char* s = (const char*)(uintptr_t)wysna_payload(v);
            return s ? strlen(s) : 0;
        }
        default: return 0;
    }
}

static inline const char* wysna_str_bytes(WVal v, char scratch[7], size_t* out_len) {
    uint32_t hi = wysna_hi(v);
    if (hi == WVAL_HI_SSO_SHORT || hi == WVAL_HI_SSO_6) {
        wysna_read_sso(v, scratch, out_len);
        return scratch;
    }
    if (hi == WVAL_HI_STR_PTR) {
        const WStringBuf* b = (const WStringBuf*)(uintptr_t)wysna_payload(v);
        if (out_len) *out_len = b ? b->byte_length : 0;
        return (b && b->data) ? b->data : "";
    }
    if (hi == WVAL_HI_CSTR) {
        const char* s = (const char*)(uintptr_t)wysna_payload(v);
        if (out_len) *out_len = s ? strlen(s) : 0;
        return s ? s : "";
    }
    if (out_len) *out_len = 0;
    return "";
}

static inline WVal wysna_str_n(const char* s, size_t length) {
    if (!s) length = 0;
    if (length <= 6) return wysna_sso_from_n(s ? s : "", length);
    if (memchr(s, '\0', length) != NULL || s[length] != '\0') {
        wysna_runtime_fail("W4003", "Unowned string > 6 bytes with embedded NUL requires WStringLiteral");
    }
    return wysna_make_hi_ptr(WVAL_HI_CSTR, s);
}

static inline WVal wysna_literal_str_n(WStringLiteral* literal, const char* data, size_t length) {
    if (length <= 6) return wysna_sso_from_n(data ? data : "", length);
    if (wysna_atomic_load_i32(&literal->buffer.refcount) == 0) {
        wysna_spinlock_acquire(&wysna_runtime_context.runtime_lock);
        if (literal->buffer.refcount == 0) {
            literal->buffer.hash_cache = 0;
            literal->buffer.byte_length = length;
            literal->buffer.codepoint_count = SIZE_MAX;
            literal->buffer.data = (char*)data;
            literal->next = wysna_g_string_literals;
            wysna_g_string_literals = literal;
            wysna_atomic_store_i32(&literal->buffer.refcount, -1); /* Immortal data and descriptor. */
        }
        wysna_spinlock_release(&wysna_runtime_context.runtime_lock);
    }
    return wysna_make_hi_ptr(WVAL_HI_STR_PTR, &literal->buffer);
}

static inline WVal wysna_str(const char* s) {
    return wysna_str_n(s, s ? strlen(s) : 0);
}

/* Single ASCII codepoints pack directly into 1-byte SSO with zero allocations. */
static inline WVal wysna_ascii_string(uint8_t codepoint) {
    if (codepoint >= 128) wysna_runtime_fail("W4003", "ASCII scalar out of bounds");
    char c = (char)codepoint;
    return wysna_sso_from_n(&c, 1);
}

/* Takes ownership of a terminated buffer; <=6B promotes to SSO and frees input buffer immediately. */
static inline WVal wysna_owned_str_n(char* data, size_t length) {
    if (length <= 6) {
        WVal sso = wysna_sso_from_n(data ? data : "", length);
        free(data);
        return sso;
    }
    WStringBuf* buffer = (WStringBuf*)wysna_alloc_safe(sizeof(WStringBuf));
    buffer->refcount = 1;
    buffer->hash_cache = 0;
    buffer->byte_length = length;
    buffer->codepoint_offsets = NULL;
    buffer->codepoint_count = SIZE_MAX;
    buffer->data = data;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_STR_PTR, buffer);
}

static inline WVal wysna_owned_str(char* data) {
    return wysna_owned_str_n(data, strlen(data));
}

static inline WVal wysna_copy_str(const char* data) {
    if (!data || *data == '\0') return wysna_sso_from_n("", 0);
    size_t length = strlen(data);
    if (length <= 6) return wysna_sso_from_n(data, length);
    char* copy = (char*)wysna_alloc_safe(length + 1);
    memcpy(copy, data, length + 1);
    return wysna_owned_str_n(copy, length);
}

static inline WVal wysna_ok(WVal inner);
static inline WVal wysna_err(WVal inner);

static inline bool wysna_builtin_name(const char* name, const char* base) {
    if (strcmp(name, base) == 0) return true;
    const char* p1 = strstr(name, "__");
    const char* p2 = strstr(base, "__");
    size_t len1 = p1 ? (size_t)(p1 - name) : strlen(name);
    size_t len2 = p2 ? (size_t)(p2 - base) : strlen(base);
    if (len1 == len2 && strncmp(name, base, len1) == 0) return true;
    size_t base_len = strlen(base);
    if (strncmp(name, base, base_len) == 0 && name[base_len] == '_' && name[base_len + 1] == '_') return true;
    size_t name_len = strlen(name);
    if (strncmp(base, name, name_len) == 0 && base[name_len] == '_' && base[name_len + 1] == '_') return true;
    return false;
}

static inline WVal wysna_enum_literal(WEnumBuf* literal, const char* enum_name, const char* variant) {
    if (wysna_builtin_name(enum_name, "Option") && strcmp(variant, "None") == 0) return wysna_none();
    if (wysna_atomic_load_i32(&literal->refcount) == 0) {
        wysna_spinlock_acquire(&wysna_runtime_context.runtime_lock);
        if (literal->refcount == 0) {
            literal->has_payload = false;
            literal->enum_name = wysna_builtin_name(enum_name, "Option") ? "Option" : enum_name;
            literal->variant = variant;
            literal->payload = wysna_unit();
            wysna_atomic_store_i32(&literal->refcount, -1);
        }
        wysna_spinlock_release(&wysna_runtime_context.runtime_lock);
    }
    return wysna_make_hi_ptr(WVAL_HI_ENUM, literal);
}

static inline WVal wysna_enum(const char* enum_name, const char* variant) {
    if (wysna_builtin_name(enum_name, "Option") && strcmp(variant, "None") == 0) return wysna_none();
    WEnumBuf* en = (WEnumBuf*)wysna_alloc_safe(sizeof(WEnumBuf));
    en->refcount = 1;
    en->has_payload = false;
    en->enum_name = wysna_builtin_name(enum_name, "Option") ? "Option" : enum_name;
    en->variant = variant;
    en->payload = wysna_unit();
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_ENUM, en);
}

static inline WVal wysna_enum_payload(const char* enum_name, const char* variant, WVal payload) {
    if (wysna_builtin_name(enum_name, "Result")) {
        if (strcmp(variant, "Ok") == 0) return wysna_ok(payload);
        if (strcmp(variant, "Err") == 0) return wysna_err(payload);
    }
    WEnumBuf* en = (WEnumBuf*)wysna_alloc_safe(sizeof(WEnumBuf));
    en->refcount = 1;
    en->has_payload = true;
    en->enum_name = wysna_builtin_name(enum_name, "Option") ? "Option" : enum_name;
    en->variant = variant;
    en->payload = payload;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_ENUM, en);
}

static inline WVal wysna_make_box(uint64_t hi, WVal inner) {
    WBoxBuf* b = (WBoxBuf*)wysna_alloc_safe(sizeof(WBoxBuf));
    b->refcount = 1;
    b->val = inner;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(hi, b);
}

static inline WVal wysna_ok(WVal inner) {
    return wysna_make_box(WVAL_HI_OK, inner);
}

static inline WVal wysna_err(WVal inner) {
    return wysna_make_box(WVAL_HI_ERR, inner);
}

static inline WVal wysna_wrap_result(WVal v, const char* err_type) {
    if (wysna_is_ok(v) || wysna_is_err(v)) return v;
    (void)err_type;
    return wysna_ok(v);
}

static WVal wysna_discard_result_value(WVal value);

/* Internal resource owners carry only a trusted host closer, never user code. */
static WVal wysna_managed_pointer(void* address, int64_t (*close)(void*)) {
    if (!address) return wysna_pointer(NULL);
    WPointerOwner* owner = (WPointerOwner*)wysna_alloc_safe(sizeof(WPointerOwner));
    owner->kind = W_POINTER;
    owner->refcount = 1;
    owner->close = close;
    owner->address = address;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_EXT, owner);
}

/* Direct WVal64 Compatibility Interface (ABI v3 unifies WVal and WVal64 natively). */
static inline bool wysna_val64_is_double(WVal64 w) {
    return wysna_is_double(w);
}

static inline bool wysna_val64_is_int(WVal64 w) {
    return wysna_hi(w) == WVAL_HI_INT;
}

static inline int64_t wysna_val64_as_int(WVal64 w) {
    return wysna_as_int(w);
}

static inline bool wysna_val64_can_pack(WVal v) {
    WTag tag = wysna_tag(v);
    switch (tag) {
        case W_UNIT:
        case W_NONE:
        case W_BOOL:
        case W_FLOAT:
            return true;
        case W_INT:
            return wysna_hi(v) == WVAL_HI_INT;
        case W_STR: {
            size_t len = wysna_str_len(v);
            return len <= 6 || wysna_str_owner(v) != NULL || wysna_hi(v) == WVAL_HI_CSTR;
        }
        default:
            return true;
    }
}

static inline WVal64 wysna_val64_pack(WVal v) {
    return v;
}

static inline WVal wysna_val64_unpack(WVal64 w) {
    return w;
}

static inline bool wysna_val64_read_sso(WVal64 w, char out[7], size_t* out_len) {
    return wysna_read_sso(w, out, out_len);
}

#endif /* WYSNA_RUNTIME_VALUES_H */
