/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_VALUES_H
#define WYSNA_RUNTIME_VALUES_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Constructors */
static inline WVal wysna_unit(void) {
    WVal v; v.tag = W_UNIT; v.as.i = 0; return v;
}

static inline WVal wysna_none(void) {
    WVal v; v.tag = W_NONE; v.as.i = 0; return v;
}

static inline WVal wysna_int(int64_t x) {
    WVal v; v.tag = W_INT; v.as.i = x; return v;
}

static inline WVal wysna_pointer(void* pointer) {
    WVal value = wysna_unit(); value.tag = W_POINTER;
    value.as.handle.address = pointer; value.as.handle.owner = NULL; return value;
}

static inline int32_t wysna_ffi_int32(WVal value) {
    if (value.tag != W_INT || value.as.i < INT32_MIN || value.as.i > INT32_MAX) {
        fprintf(stderr, "[W4003] RuntimeError: int32 FFI argument out of range\n"); exit(1);
    }
    return (int32_t)value.as.i;
}

static inline WVal wysna_float(double x) {
    WVal v; v.tag = W_FLOAT; v.as.f = x; return v;
}

static inline WVal wysna_bool(bool x) {
    WVal v; v.tag = W_BOOL; v.as.b = x; return v;
}

static inline WVal wysna_str_n(const char* s, size_t length) {
    WVal v; v.tag = W_STR; v.as.string.owner = NULL;
    v.as.string.data = s ? s : ""; v.as.string.length = s ? length : 0; return v;
}

static inline WVal wysna_literal_str_n(WStringLiteral* literal, const char* data, size_t length) {
    if (literal->buffer.refcount == 0) {
        literal->buffer.refcount = -1; /* Immortal data and descriptor. */
        literal->buffer.byte_length = length;
        literal->buffer.codepoint_count = SIZE_MAX;
        literal->buffer.data = (char*)data;
        literal->next = wysna_g_string_literals;
        wysna_g_string_literals = literal;
    }
    WVal value = wysna_str_n(data, length);
    value.as.string.owner = &literal->buffer;
    return value;
}

static inline WVal wysna_str(const char* s) {
    return wysna_str_n(s, s ? strlen(s) : 0);
}

/* Immutable ASCII scalars have static lifetime, including the one-byte NUL string. */
static inline WVal wysna_ascii_string(uint8_t codepoint) {
    static const char scalars[] =
        "\000\000\001\000\002\000\003\000\004\000\005\000\006\000\007\000\010\000\011\000\012\000\013\000\014\000\015\000\016\000\017\000"
        "\020\000\021\000\022\000\023\000\024\000\025\000\026\000\027\000\030\000\031\000\032\000\033\000\034\000\035\000\036\000\037\000"
        "\040\000\041\000\042\000\043\000\044\000\045\000\046\000\047\000\050\000\051\000\052\000\053\000\054\000\055\000\056\000\057\000"
        "\060\000\061\000\062\000\063\000\064\000\065\000\066\000\067\000\070\000\071\000\072\000\073\000\074\000\075\000\076\000\077\000"
        "\100\000\101\000\102\000\103\000\104\000\105\000\106\000\107\000\110\000\111\000\112\000\113\000\114\000\115\000\116\000\117\000"
        "\120\000\121\000\122\000\123\000\124\000\125\000\126\000\127\000\130\000\131\000\132\000\133\000\134\000\135\000\136\000\137\000"
        "\140\000\141\000\142\000\143\000\144\000\145\000\146\000\147\000\150\000\151\000\152\000\153\000\154\000\155\000\156\000\157\000"
        "\160\000\161\000\162\000\163\000\164\000\165\000\166\000\167\000\170\000\171\000\172\000\173\000\174\000\175\000\176\000\177\000";
    if (codepoint >= 128) wysna_runtime_fail("W4003", "ASCII scalar out of bounds");
    return wysna_str_n(scalars + (size_t)codepoint * 2, 1);
}

/* Takes ownership of a terminated buffer; length includes embedded NUL bytes. */
static inline WVal wysna_owned_str_n(char* data, size_t length) {
    WStringBuf* buffer = (WStringBuf*)wysna_alloc_safe(sizeof(WStringBuf));
    buffer->refcount = 1;
    buffer->byte_length = length;
    buffer->codepoint_offsets = NULL;
    buffer->codepoint_count = SIZE_MAX;
    buffer->data = data;
    wysna_mem_on_alloc();
    WVal value = wysna_str_n(data, length);
    value.as.string.owner = buffer;
    return value;
}

static inline WVal wysna_owned_str(char* data) {
    return wysna_owned_str_n(data, strlen(data));
}

static inline WVal wysna_copy_str(const char* data) {
    size_t length = strlen(data);
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

static inline WVal wysna_enum(const char* enum_name, const char* variant) {
    if (wysna_builtin_name(enum_name, "Option") && strcmp(variant, "None") == 0) return wysna_none();
    WVal v;
    v.tag = W_ENUM;
    v.as.en.enum_name = wysna_builtin_name(enum_name, "Option") ? "Option" : enum_name;
    v.as.en.variant = variant;
    v.as.en.box = NULL;
    return v;
}

static inline WVal wysna_enum_payload(const char* enum_name, const char* variant, WVal payload) {
    if (wysna_builtin_name(enum_name, "Result")) {
        if (strcmp(variant, "Ok") == 0) return wysna_ok(payload);
        if (strcmp(variant, "Err") == 0) return wysna_err(payload);
    }
    WBoxBuf* b = (WBoxBuf*)wysna_alloc_safe(sizeof(WBoxBuf));
    b->refcount = 1;
    b->val = payload;
    wysna_mem_on_alloc();
    WVal v;
    v.tag = W_ENUM;
    v.as.en.enum_name = wysna_builtin_name(enum_name, "Option") ? "Option" : enum_name;
    v.as.en.variant = variant;
    v.as.en.box = b;
    return v;
}

static inline WVal wysna_ok(WVal inner) {
    WBoxBuf* b = (WBoxBuf*)wysna_alloc_safe(sizeof(WBoxBuf));
    b->refcount = 1;
    b->val = inner;
    wysna_mem_on_alloc();
    WVal v;
    v.tag = W_OK;
    v.as.box = b;
    return v;
}

static inline WVal wysna_err(WVal inner) {
    WBoxBuf* b = (WBoxBuf*)wysna_alloc_safe(sizeof(WBoxBuf));
    b->refcount = 1;
    b->val = inner;
    wysna_mem_on_alloc();
    WVal v;
    v.tag = W_ERR;
    v.as.box = b;
    return v;
}

static inline WVal wysna_wrap_result(WVal v, const char* err_type) {
    if (v.tag == W_OK || v.tag == W_ERR) return v;
    (void)err_type;
    return wysna_ok(v);
}

static WVal wysna_discard_result_value(WVal value);

/* Internal resource owners carry only a trusted host closer, never user code. */
static WVal wysna_managed_pointer(void* address, int64_t (*close)(void*)) {
    WVal value = wysna_pointer(address);
    if (!address) return value;
    WPointerOwner* owner = (WPointerOwner*)wysna_alloc_safe(sizeof(WPointerOwner));
    owner->refcount = 1; owner->close = close; owner->address = address;
    value.as.handle.owner = owner;
    wysna_mem_on_alloc();
    return value;
}

#endif /* WYSNA_RUNTIME_VALUES_H */
