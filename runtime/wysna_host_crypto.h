/* Internal runtime domain. Include wysna_runtime.h or wysna_host.h rather than this file directly. */
#ifndef WYSNA_HOST_CRYPTO_H
#define WYSNA_HOST_CRYPTO_H

#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#if defined(_WIN32)
#include <windows.h>
#include <wincrypt.h>
static inline int wysna_crypto_fill_random(uint8_t* buf, size_t count) {
    HCRYPTPROV prov;
    if (!CryptAcquireContext(&prov, NULL, NULL, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
        return -1;
    }
    BOOL ok = CryptGenRandom(prov, (DWORD)count, buf);
    CryptReleaseContext(prov, 0);
    return ok ? 0 : -1;
}
#else
#include <fcntl.h>
#include <unistd.h>
static inline int wysna_crypto_fill_random(uint8_t* buf, size_t count) {
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd < 0) return -1;
    size_t total = 0;
    while (total < count) {
        ssize_t n = read(fd, buf + total, count - total);
        if (n <= 0) { close(fd); return -1; }
        total += (size_t)n;
    }
    close(fd);
    return 0;
}
#endif

/* -------------------------------------------------------------------------
 * SHA-256 Implementation (FIPS 180-4)
 * ------------------------------------------------------------------------- */
#define WYSNA_SHA256_ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
#define WYSNA_SHA256_CH(x, y, z) (((x) & (y)) ^ (~(x) & (z)))
#define WYSNA_SHA256_MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define WYSNA_SHA256_EP0(x) (WYSNA_SHA256_ROTR(x, 2) ^ WYSNA_SHA256_ROTR(x, 13) ^ WYSNA_SHA256_ROTR(x, 22))
#define WYSNA_SHA256_EP1(x) (WYSNA_SHA256_ROTR(x, 6) ^ WYSNA_SHA256_ROTR(x, 11) ^ WYSNA_SHA256_ROTR(x, 25))
#define WYSNA_SHA256_SIG0(x) (WYSNA_SHA256_ROTR(x, 7) ^ WYSNA_SHA256_ROTR(x, 18) ^ ((x) >> 3))
#define WYSNA_SHA256_SIG1(x) (WYSNA_SHA256_ROTR(x, 17) ^ WYSNA_SHA256_ROTR(x, 19) ^ ((x) >> 10))

static const uint32_t wysna_sha256_k[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

typedef struct {
    uint8_t data[64];
    uint32_t datalen;
    uint64_t bitlen;
    uint32_t state[8];
} WysnaSha256Context;

static void wysna_sha256_transform(WysnaSha256Context* ctx, const uint8_t data[]) {
    uint32_t a, b, c, d, e, f, g, h, i, j, t1, t2, m[64];

    for (i = 0, j = 0; i < 16; ++i, j += 4)
        m[i] = ((uint32_t)data[j] << 24) | ((uint32_t)data[j + 1] << 16) | ((uint32_t)data[j + 2] << 8) | ((uint32_t)data[j + 3]);
    for (; i < 64; ++i)
        m[i] = WYSNA_SHA256_SIG1(m[i - 2]) + m[i - 7] + WYSNA_SHA256_SIG0(m[i - 15]) + m[i - 16];

    a = ctx->state[0];
    b = ctx->state[1];
    c = ctx->state[2];
    d = ctx->state[3];
    e = ctx->state[4];
    f = ctx->state[5];
    g = ctx->state[6];
    h = ctx->state[7];

    for (i = 0; i < 64; ++i) {
        t1 = h + WYSNA_SHA256_EP1(e) + WYSNA_SHA256_CH(e, f, g) + wysna_sha256_k[i] + m[i];
        t2 = WYSNA_SHA256_EP0(a) + WYSNA_SHA256_MAJ(a, b, c);
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }

    ctx->state[0] += a;
    ctx->state[1] += b;
    ctx->state[2] += c;
    ctx->state[3] += d;
    ctx->state[4] += e;
    ctx->state[5] += f;
    ctx->state[6] += g;
    ctx->state[7] += h;
}

static void wysna_sha256_init(WysnaSha256Context* ctx) {
    ctx->datalen = 0;
    ctx->bitlen = 0;
    ctx->state[0] = 0x6a09e667;
    ctx->state[1] = 0xbb67ae85;
    ctx->state[2] = 0x3c6ef372;
    ctx->state[3] = 0xa54ff53a;
    ctx->state[4] = 0x510e527f;
    ctx->state[5] = 0x9b05688c;
    ctx->state[6] = 0x1f83d9ab;
    ctx->state[7] = 0x5be0cd19;
}

static void wysna_sha256_update(WysnaSha256Context* ctx, const uint8_t data[], size_t len) {
    for (size_t i = 0; i < len; ++i) {
        ctx->data[ctx->datalen] = data[i];
        ctx->datalen++;
        if (ctx->datalen == 64) {
            wysna_sha256_transform(ctx, ctx->data);
            ctx->bitlen += 512;
            ctx->datalen = 0;
        }
    }
}

static void wysna_sha256_final(WysnaSha256Context* ctx, uint8_t hash[]) {
    uint32_t i = ctx->datalen;

    if (ctx->datalen < 56) {
        ctx->data[i++] = 0x80;
        while (i < 56) ctx->data[i++] = 0x00;
    } else {
        ctx->data[i++] = 0x80;
        while (i < 64) ctx->data[i++] = 0x00;
        wysna_sha256_transform(ctx, ctx->data);
        memset(ctx->data, 0, 56);
    }

    ctx->bitlen += (uint64_t)ctx->datalen * 8;
    ctx->data[63] = (uint8_t)(ctx->bitlen);
    ctx->data[62] = (uint8_t)(ctx->bitlen >> 8);
    ctx->data[61] = (uint8_t)(ctx->bitlen >> 16);
    ctx->data[60] = (uint8_t)(ctx->bitlen >> 24);
    ctx->data[59] = (uint8_t)(ctx->bitlen >> 32);
    ctx->data[58] = (uint8_t)(ctx->bitlen >> 40);
    ctx->data[57] = (uint8_t)(ctx->bitlen >> 48);
    ctx->data[56] = (uint8_t)(ctx->bitlen >> 56);
    wysna_sha256_transform(ctx, ctx->data);

    for (i = 0; i < 4; ++i) {
        hash[i]      = (uint8_t)((ctx->state[0] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 4]  = (uint8_t)((ctx->state[1] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 8]  = (uint8_t)((ctx->state[2] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 12] = (uint8_t)((ctx->state[3] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 16] = (uint8_t)((ctx->state[4] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 20] = (uint8_t)((ctx->state[5] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 24] = (uint8_t)((ctx->state[6] >> (24 - i * 8)) & 0x000000ff);
        hash[i + 28] = (uint8_t)((ctx->state[7] >> (24 - i * 8)) & 0x000000ff);
    }
}

static void wysna_sha256_compute(const uint8_t* data, size_t len, uint8_t out[32]) {
    WysnaSha256Context ctx;
    wysna_sha256_init(&ctx);
    wysna_sha256_update(&ctx, data, len);
    wysna_sha256_final(&ctx, out);
}

/* -------------------------------------------------------------------------
 * HMAC-SHA256 Implementation (RFC 2104)
 * ------------------------------------------------------------------------- */
static void wysna_hmac_sha256_compute(const uint8_t* key, size_t key_len,
                                      const uint8_t* msg, size_t msg_len,
                                      uint8_t out[32]) {
    uint8_t k_block[64];
    memset(k_block, 0, 64);
    if (key_len > 64) {
        wysna_sha256_compute(key, key_len, k_block);
    } else {
        memcpy(k_block, key, key_len);
    }

    uint8_t k_ipad[64];
    uint8_t k_opad[64];
    for (int i = 0; i < 64; ++i) {
        k_ipad[i] = k_block[i] ^ 0x36;
        k_opad[i] = k_block[i] ^ 0x5c;
    }

    uint8_t inner_hash[32];
    WysnaSha256Context inner_ctx;
    wysna_sha256_init(&inner_ctx);
    wysna_sha256_update(&inner_ctx, k_ipad, 64);
    wysna_sha256_update(&inner_ctx, msg, msg_len);
    wysna_sha256_final(&inner_ctx, inner_hash);

    WysnaSha256Context outer_ctx;
    wysna_sha256_init(&outer_ctx);
    wysna_sha256_update(&outer_ctx, k_opad, 64);
    wysna_sha256_update(&outer_ctx, inner_hash, 32);
    wysna_sha256_final(&outer_ctx, out);
}

/* -------------------------------------------------------------------------
 * SHA-512 Implementation (FIPS 180-4)
 * ------------------------------------------------------------------------- */
#define WYSNA_SHA512_ROTR(x, n) (((x) >> (n)) | ((x) << (64 - (n))))
#define WYSNA_SHA512_CH(x, y, z) (((x) & (y)) ^ (~(x) & (z)))
#define WYSNA_SHA512_MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define WYSNA_SHA512_EP0(x) (WYSNA_SHA512_ROTR(x, 28) ^ WYSNA_SHA512_ROTR(x, 34) ^ WYSNA_SHA512_ROTR(x, 39))
#define WYSNA_SHA512_EP1(x) (WYSNA_SHA512_ROTR(x, 14) ^ WYSNA_SHA512_ROTR(x, 18) ^ WYSNA_SHA512_ROTR(x, 41))
#define WYSNA_SHA512_SIG0(x) (WYSNA_SHA512_ROTR(x, 1) ^ WYSNA_SHA512_ROTR(x, 8) ^ ((x) >> 7))
#define WYSNA_SHA512_SIG1(x) (WYSNA_SHA512_ROTR(x, 19) ^ WYSNA_SHA512_ROTR(x, 61) ^ ((x) >> 6))

static const uint64_t wysna_sha512_k[80] = {
    0x428a2f98d728ae22ULL, 0x7137449123ef65cdULL, 0xb5c0fbcfec4d3b2fULL, 0xe9b5dba58189dbbcULL,
    0x3956c25bf348b538ULL, 0x59f111f1b605d019ULL, 0x923f82a4af194f9bULL, 0xab1c5ed5da6d8118ULL,
    0xd807aa98a3030242ULL, 0x12835b0145706fbeULL, 0x243185be4ee4b28cULL, 0x550c7dc3d5ffb4e2ULL,
    0x72be5d74f27b896fULL, 0x80deb1fe3b1696b1ULL, 0x9bdc06a725c71235ULL, 0xc19bf174cf692694ULL,
    0xe49b69c19ef14ad2ULL, 0xefbe4786384f25e3ULL, 0x0fc19dc68b8cd5b5ULL, 0x240ca1cc77ac9c65ULL,
    0x2de92c6f592b0275ULL, 0x4a7484aa6ea6e483ULL, 0x5cb0a9dcbd41fbd4ULL, 0x76f988da831153b5ULL,
    0x983e5152ee66dfabULL, 0xa831c66d2db43210ULL, 0xb00327c898fb213fULL, 0xbf597fc7beef0ee4ULL,
    0xc6e00bf33da88fc2ULL, 0xd5a79147930aa725ULL, 0x06ca6351e003826fULL, 0x142929670a0e6e70ULL,
    0x27b70a8546d22ffcULL, 0x2e1b21385c26c926ULL, 0x4d2c6dfc5ac42aedULL, 0x53380d139d95b3dfULL,
    0x650a73548baf63deULL, 0x766a0abb3c77b2a8ULL, 0x81c2c92e47edaee6ULL, 0x92722c851482353bULL,
    0xa2bfe8a14cf10364ULL, 0xa81a664bbc423001ULL, 0xc24b8b70d0f89791ULL, 0xc76c51a30654be30ULL,
    0xd192e819d6ef5218ULL, 0xd69906245565a910ULL, 0xf40e35855771202aULL, 0x106aa07032bbd1b8ULL,
    0x19a4c116b8d2d0c8ULL, 0x1e376c085141ab53ULL, 0x2748774cdf8eeb99ULL, 0x34b0bcb5e19b48a8ULL,
    0x391c0cb3c5c95a63ULL, 0x4ed8aa4ae3418acbULL, 0x5b9cca4f7763e373ULL, 0x682e6ff3d6b2b8a3ULL,
    0x748f82ee5defb2fcULL, 0x78a5636f43172f60ULL, 0x84c87814a1f0ab72ULL, 0x8cc702081a6439ecULL,
    0x90befffa23631e28ULL, 0xa4506cebde82bde9ULL, 0xbef9a3f7b2c67915ULL, 0xc67178f2e372532bULL,
    0xca273eceea26619cULL, 0xd186b8c721c0c207ULL, 0xeada7dd6cde0eb1eULL, 0xf57d4f7fee6ed178ULL,
    0x06f067aa72176fbaULL, 0x0a637dc5a2c898a6ULL, 0x113f9804bef90daeULL, 0x1b710b35131c471bULL,
    0x28db77f523047d84ULL, 0x32caab7b40c72493ULL, 0x3c9ebe0a15c9bebcULL, 0x431d67c49c100d4cULL,
    0x4cc5d4becb3e42b6ULL, 0x597f299cfc657e2aULL, 0x5fcb6fab3ad6faecULL, 0x6c44198c4a475817ULL
};

typedef struct {
    uint8_t data[128];
    uint32_t datalen;
    uint64_t bitlen[2];
    uint64_t state[8];
} WysnaSha512Context;

static void wysna_sha512_transform(WysnaSha512Context* ctx, const uint8_t data[]) {
    uint64_t a, b, c, d, e, f, g, h, t1, t2, m[80];
    int i, j;

    for (i = 0, j = 0; i < 16; ++i, j += 8) {
        m[i] = ((uint64_t)data[j] << 56) | ((uint64_t)data[j + 1] << 48) |
               ((uint64_t)data[j + 2] << 40) | ((uint64_t)data[j + 3] << 32) |
               ((uint64_t)data[j + 4] << 24) | ((uint64_t)data[j + 5] << 16) |
               ((uint64_t)data[j + 6] << 8) | ((uint64_t)data[j + 7]);
    }
    for (; i < 80; ++i) {
        m[i] = WYSNA_SHA512_SIG1(m[i - 2]) + m[i - 7] + WYSNA_SHA512_SIG0(m[i - 15]) + m[i - 16];
    }

    a = ctx->state[0]; b = ctx->state[1]; c = ctx->state[2]; d = ctx->state[3];
    e = ctx->state[4]; f = ctx->state[5]; g = ctx->state[6]; h = ctx->state[7];

    for (i = 0; i < 80; ++i) {
        t1 = h + WYSNA_SHA512_EP1(e) + WYSNA_SHA512_CH(e, f, g) + wysna_sha512_k[i] + m[i];
        t2 = WYSNA_SHA512_EP0(a) + WYSNA_SHA512_MAJ(a, b, c);
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

static void wysna_sha512_init(WysnaSha512Context* ctx) {
    ctx->datalen = 0;
    ctx->bitlen[0] = ctx->bitlen[1] = 0;
    ctx->state[0] = 0x6a09e667f3bcc908ULL;
    ctx->state[1] = 0xbb67ae8584caa73bULL;
    ctx->state[2] = 0x3c6ef372fe94f82bULL;
    ctx->state[3] = 0xa54ff53a5f1d36f1ULL;
    ctx->state[4] = 0x510e527fade682d1ULL;
    ctx->state[5] = 0x9b05688c2b3e6c1fULL;
    ctx->state[6] = 0x1f83d9abfb41bd6bULL;
    ctx->state[7] = 0x5be0cd19137e2179ULL;
}

static void wysna_sha512_update(WysnaSha512Context* ctx, const uint8_t data[], size_t len) {
    for (size_t i = 0; i < len; ++i) {
        ctx->data[ctx->datalen++] = data[i];
        if (ctx->datalen == 128) {
            wysna_sha512_transform(ctx, ctx->data);
            if ((ctx->bitlen[0] += 1024) < 1024) ctx->bitlen[1]++;
            ctx->datalen = 0;
        }
    }
}

static void wysna_sha512_final(WysnaSha512Context* ctx, uint8_t hash[64]) {
    uint32_t i = ctx->datalen;
    if (ctx->datalen < 112) {
        ctx->data[i++] = 0x80;
        while (i < 112) ctx->data[i++] = 0x00;
    } else {
        ctx->data[i++] = 0x80;
        while (i < 128) ctx->data[i++] = 0x00;
        wysna_sha512_transform(ctx, ctx->data);
        memset(ctx->data, 0, 112);
    }
    uint64_t add_bits = (uint64_t)ctx->datalen * 8;
    if ((ctx->bitlen[0] += add_bits) < add_bits) ctx->bitlen[1]++;

    for (int k = 0; k < 8; k++) {
        ctx->data[127 - k] = (uint8_t)(ctx->bitlen[0] >> (k * 8));
        ctx->data[119 - k] = (uint8_t)(ctx->bitlen[1] >> (k * 8));
    }
    wysna_sha512_transform(ctx, ctx->data);

    for (i = 0; i < 8; ++i) {
        for (int k = 0; k < 8; k++) {
            hash[i * 8 + k] = (uint8_t)((ctx->state[i] >> (56 - k * 8)) & 0xff);
        }
    }
}

static void wysna_sha512_compute(const uint8_t* data, size_t len, uint8_t out[64]) {
    WysnaSha512Context ctx;
    wysna_sha512_init(&ctx);
    wysna_sha512_update(&ctx, data, len);
    wysna_sha512_final(&ctx, out);
}

/* -------------------------------------------------------------------------
 * HMAC-SHA512 Implementation (RFC 2104)
 * ------------------------------------------------------------------------- */
static void wysna_hmac_sha512_compute(const uint8_t* key, size_t key_len,
                                      const uint8_t* msg, size_t msg_len,
                                      uint8_t out[64]) {
    uint8_t k_block[128];
    memset(k_block, 0, 128);
    if (key_len > 128) {
        wysna_sha512_compute(key, key_len, k_block);
    } else {
        memcpy(k_block, key, key_len);
    }

    uint8_t k_ipad[128];
    uint8_t k_opad[128];
    for (int i = 0; i < 128; ++i) {
        k_ipad[i] = k_block[i] ^ 0x36;
        k_opad[i] = k_block[i] ^ 0x5c;
    }

    uint8_t inner_hash[64];
    WysnaSha512Context inner_ctx;
    wysna_sha512_init(&inner_ctx);
    wysna_sha512_update(&inner_ctx, k_ipad, 128);
    wysna_sha512_update(&inner_ctx, msg, msg_len);
    wysna_sha512_final(&inner_ctx, inner_hash);

    WysnaSha512Context outer_ctx;
    wysna_sha512_init(&outer_ctx);
    wysna_sha512_update(&outer_ctx, k_opad, 128);
    wysna_sha512_update(&outer_ctx, inner_hash, 64);
    wysna_sha512_final(&outer_ctx, out);
}

/* -------------------------------------------------------------------------
 * MD5 Implementation (RFC 1321)
 * ------------------------------------------------------------------------- */
typedef struct {
    uint32_t state[4];
    uint32_t count[2];
    uint8_t buffer[64];
} WysnaMd5Context;

#define WYSNA_MD5_F(x, y, z) (((x) & (y)) | ((~x) & (z)))
#define WYSNA_MD5_G(x, y, z) (((x) & (z)) | ((y) & (~z)))
#define WYSNA_MD5_H(x, y, z) ((x) ^ (y) ^ (z))
#define WYSNA_MD5_I(x, y, z) ((y) ^ ((x) | (~z)))

#define WYSNA_MD5_ROTL(x, n) (((x) << (n)) | ((x) >> (32 - (n))))

#define WYSNA_MD5_FF(a, b, c, d, x, s, ac) { \
    (a) += WYSNA_MD5_F((b), (c), (d)) + (x) + (uint32_t)(ac); \
    (a) = WYSNA_MD5_ROTL((a), (s)); \
    (a) += (b); \
}
#define WYSNA_MD5_GG(a, b, c, d, x, s, ac) { \
    (a) += WYSNA_MD5_G((b), (c), (d)) + (x) + (uint32_t)(ac); \
    (a) = WYSNA_MD5_ROTL((a), (s)); \
    (a) += (b); \
}
#define WYSNA_MD5_HH(a, b, c, d, x, s, ac) { \
    (a) += WYSNA_MD5_H((b), (c), (d)) + (x) + (uint32_t)(ac); \
    (a) = WYSNA_MD5_ROTL((a), (s)); \
    (a) += (b); \
}
#define WYSNA_MD5_II(a, b, c, d, x, s, ac) { \
    (a) += WYSNA_MD5_I((b), (c), (d)) + (x) + (uint32_t)(ac); \
    (a) = WYSNA_MD5_ROTL((a), (s)); \
    (a) += (b); \
}

static void wysna_md5_init(WysnaMd5Context* ctx) {
    ctx->count[0] = ctx->count[1] = 0;
    ctx->state[0] = 0x67452301;
    ctx->state[1] = 0xefcdab89;
    ctx->state[2] = 0x98badcfe;
    ctx->state[3] = 0x10325476;
}

static void wysna_md5_transform(uint32_t state[4], const uint8_t block[64]) {
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3], x[16];
    for (int i = 0, j = 0; i < 16; i++, j += 4) {
        x[i] = ((uint32_t)block[j]) | (((uint32_t)block[j + 1]) << 8) |
               (((uint32_t)block[j + 2]) << 16) | (((uint32_t)block[j + 3]) << 24);
    }

    /* Round 1 */
    WYSNA_MD5_FF(a, b, c, d, x[ 0],  7, 0xd76aa478);
    WYSNA_MD5_FF(d, a, b, c, x[ 1], 12, 0xe8c7b756);
    WYSNA_MD5_FF(c, d, a, b, x[ 2], 17, 0x242070db);
    WYSNA_MD5_FF(b, c, d, a, x[ 3], 22, 0xc1bdceee);
    WYSNA_MD5_FF(a, b, c, d, x[ 4],  7, 0xf57c0faf);
    WYSNA_MD5_FF(d, a, b, c, x[ 5], 12, 0x4787c62a);
    WYSNA_MD5_FF(c, d, a, b, x[ 6], 17, 0xa8304613);
    WYSNA_MD5_FF(b, c, d, a, x[ 7], 22, 0xfd469501);
    WYSNA_MD5_FF(a, b, c, d, x[ 8],  7, 0x698098d8);
    WYSNA_MD5_FF(d, a, b, c, x[ 9], 12, 0x8b44f7af);
    WYSNA_MD5_FF(c, d, a, b, x[10], 17, 0xffff5bb1);
    WYSNA_MD5_FF(b, c, d, a, x[11], 22, 0x895cd7be);
    WYSNA_MD5_FF(a, b, c, d, x[12],  7, 0x6b901122);
    WYSNA_MD5_FF(d, a, b, c, x[13], 12, 0xfd987193);
    WYSNA_MD5_FF(c, d, a, b, x[14], 17, 0xa679438e);
    WYSNA_MD5_FF(b, c, d, a, x[15], 22, 0x49b40821);

    /* Round 2 */
    WYSNA_MD5_GG(a, b, c, d, x[ 1],  5, 0xf61e2562);
    WYSNA_MD5_GG(d, a, b, c, x[ 6],  9, 0xc040b340);
    WYSNA_MD5_GG(c, d, a, b, x[11], 14, 0x265e5a51);
    WYSNA_MD5_GG(b, c, d, a, x[ 0], 20, 0xe9b6c7aa);
    WYSNA_MD5_GG(a, b, c, d, x[ 5],  5, 0xd62f105d);
    WYSNA_MD5_GG(d, a, b, c, x[10],  9, 0x02441453);
    WYSNA_MD5_GG(c, d, a, b, x[15], 14, 0xd8a1e681);
    WYSNA_MD5_GG(b, c, d, a, x[ 4], 20, 0xe7d3fbc8);
    WYSNA_MD5_GG(a, b, c, d, x[ 9],  5, 0x21e1cde6);
    WYSNA_MD5_GG(d, a, b, c, x[14],  9, 0xc33707d6);
    WYSNA_MD5_GG(c, d, a, b, x[ 3], 14, 0xf4d50d87);
    WYSNA_MD5_GG(b, c, d, a, x[ 8], 20, 0x455a14ed);
    WYSNA_MD5_GG(a, b, c, d, x[13],  5, 0xa9e3e905);
    WYSNA_MD5_GG(d, a, b, c, x[ 2],  9, 0xfcefa3f8);
    WYSNA_MD5_GG(c, d, a, b, x[ 7], 14, 0x676f02d9);
    WYSNA_MD5_GG(b, c, d, a, x[12], 20, 0x8d2a4c8a);

    /* Round 3 */
    WYSNA_MD5_HH(a, b, c, d, x[ 5],  4, 0xfffa3942);
    WYSNA_MD5_HH(d, a, b, c, x[ 8], 11, 0x8771f681);
    WYSNA_MD5_HH(c, d, a, b, x[11], 16, 0x6d9d6122);
    WYSNA_MD5_HH(b, c, d, a, x[14], 23, 0xfde5380c);
    WYSNA_MD5_HH(a, b, c, d, x[ 1],  4, 0xa4beea44);
    WYSNA_MD5_HH(d, a, b, c, x[ 4], 11, 0x4bdecfa9);
    WYSNA_MD5_HH(c, d, a, b, x[ 7], 16, 0xf6bb4b60);
    WYSNA_MD5_HH(b, c, d, a, x[10], 23, 0xbebfbc70);
    WYSNA_MD5_HH(a, b, c, d, x[13],  4, 0x289b7ec6);
    WYSNA_MD5_HH(d, a, b, c, x[ 0], 11, 0xeaa127fa);
    WYSNA_MD5_HH(c, d, a, b, x[ 3], 16, 0xd4ef3085);
    WYSNA_MD5_HH(b, c, d, a, x[ 6], 23, 0x04881d05);
    WYSNA_MD5_HH(a, b, c, d, x[ 9],  4, 0xd9d4d039);
    WYSNA_MD5_HH(d, a, b, c, x[12], 11, 0xe6db99e5);
    WYSNA_MD5_HH(c, d, a, b, x[15], 16, 0x1fa27cf8);
    WYSNA_MD5_HH(b, c, d, a, x[ 2], 23, 0xc4ac5665);

    /* Round 4 */
    WYSNA_MD5_II(a, b, c, d, x[ 0],  6, 0xf4292244);
    WYSNA_MD5_II(d, a, b, c, x[ 7], 10, 0x432aff97);
    WYSNA_MD5_II(c, d, a, b, x[14], 15, 0xab9423a7);
    WYSNA_MD5_II(b, c, d, a, x[ 5], 21, 0xfc93a039);
    WYSNA_MD5_II(a, b, c, d, x[12],  6, 0x655b59c3);
    WYSNA_MD5_II(d, a, b, c, x[ 3], 10, 0x8f0ccc92);
    WYSNA_MD5_II(c, d, a, b, x[10], 15, 0xffeff47d);
    WYSNA_MD5_II(b, c, d, a, x[ 1], 21, 0x85845dd1);
    WYSNA_MD5_II(a, b, c, d, x[ 8],  6, 0x6fa87e4f);
    WYSNA_MD5_II(d, a, b, c, x[15], 10, 0xfe2ce6e0);
    WYSNA_MD5_II(c, d, a, b, x[ 6], 15, 0xa3014314);
    WYSNA_MD5_II(b, c, d, a, x[13], 21, 0x4e0811a1);
    WYSNA_MD5_II(a, b, c, d, x[ 4],  6, 0xf7537e82);
    WYSNA_MD5_II(d, a, b, c, x[11], 10, 0xbd3af235);
    WYSNA_MD5_II(c, d, a, b, x[ 2], 15, 0x2ad7d2bb);
    WYSNA_MD5_II(b, c, d, a, x[ 9], 21, 0xeb86d391);

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
}

static void wysna_md5_update(WysnaMd5Context* ctx, const uint8_t* input, size_t input_len) {
    size_t i, index, part_len;
    index = (size_t)((ctx->count[0] >> 3) & 0x3f);
    if ((ctx->count[0] += ((uint32_t)input_len << 3)) < ((uint32_t)input_len << 3)) {
        ctx->count[1]++;
    }
    ctx->count[1] += ((uint32_t)input_len >> 29);
    part_len = 64 - index;

    if (input_len >= part_len) {
        memcpy(&ctx->buffer[index], input, part_len);
        wysna_md5_transform(ctx->state, ctx->buffer);
        for (i = part_len; i + 63 < input_len; i += 64) {
            wysna_md5_transform(ctx->state, &input[i]);
        }
        index = 0;
    } else {
        i = 0;
    }
    memcpy(&ctx->buffer[index], &input[i], input_len - i);
}

static void wysna_md5_final(WysnaMd5Context* ctx, uint8_t digest[16]) {
    uint8_t bits[8];
    for (int i = 0; i < 4; i++) {
        bits[i] = (uint8_t)((ctx->count[0] >> (i * 8)) & 0xff);
        bits[i + 4] = (uint8_t)((ctx->count[1] >> (i * 8)) & 0xff);
    }
    size_t index = (size_t)((ctx->count[0] >> 3) & 0x3f);
    size_t pad_len = (index < 56) ? (56 - index) : (120 - index);
    static const uint8_t padding[64] = { 0x80, 0 };
    wysna_md5_update(ctx, padding, pad_len);
    wysna_md5_update(ctx, bits, 8);
    for (int i = 0; i < 4; i++) {
        for (int j = 0; j < 4; j++) {
            digest[i * 4 + j] = (uint8_t)((ctx->state[i] >> (j * 8)) & 0xff);
        }
    }
}

static void wysna_md5_compute(const uint8_t* data, size_t len, uint8_t out[16]) {
    WysnaMd5Context ctx;
    wysna_md5_init(&ctx);
    wysna_md5_update(&ctx, data, len);
    wysna_md5_final(&ctx, out);
}

/* -------------------------------------------------------------------------
 * Host ABI Functions
 * ------------------------------------------------------------------------- */
static inline int64_t wysna_host_crypto_sha256(void* input_handle, void* output_handle) {
    WysnaHostEntry* input = wysna_host_find(input_handle, 1);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!input || !output) return -1;

    uint8_t digest[32];
    wysna_sha256_compute((const uint8_t*)input->data, input->length, digest);

    if (output->capacity < 32) {
        free(output->data);
        output->data = wysna_alloc_safe(32);
        output->capacity = 32;
    }
    memcpy(output->data, digest, 32);
    output->length = 32;
    return 0;
}

static inline int64_t wysna_host_crypto_hmac_sha256(void* key_handle, void* msg_handle, void* output_handle) {
    WysnaHostEntry* key = wysna_host_find(key_handle, 1);
    WysnaHostEntry* msg = wysna_host_find(msg_handle, 1);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!key || !msg || !output) return -1;

    uint8_t digest[32];
    wysna_hmac_sha256_compute((const uint8_t*)key->data, key->length,
                              (const uint8_t*)msg->data, msg->length,
                              digest);

    if (output->capacity < 32) {
        free(output->data);
        output->data = wysna_alloc_safe(32);
        output->capacity = 32;
    }
    memcpy(output->data, digest, 32);
    output->length = 32;
    return 0;
}

static inline int64_t wysna_host_crypto_sha512(void* input_handle, void* output_handle) {
    WysnaHostEntry* input = wysna_host_find(input_handle, 1);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!input || !output) return -1;

    uint8_t digest[64];
    wysna_sha512_compute((const uint8_t*)input->data, input->length, digest);

    if (output->capacity < 64) {
        free(output->data);
        output->data = wysna_alloc_safe(64);
        output->capacity = 64;
    }
    memcpy(output->data, digest, 64);
    output->length = 64;
    return 0;
}

static inline int64_t wysna_host_crypto_hmac_sha512(void* key_handle, void* msg_handle, void* output_handle) {
    WysnaHostEntry* key = wysna_host_find(key_handle, 1);
    WysnaHostEntry* msg = wysna_host_find(msg_handle, 1);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!key || !msg || !output) return -1;

    uint8_t digest[64];
    wysna_hmac_sha512_compute((const uint8_t*)key->data, key->length,
                              (const uint8_t*)msg->data, msg->length,
                              digest);

    if (output->capacity < 64) {
        free(output->data);
        output->data = wysna_alloc_safe(64);
        output->capacity = 64;
    }
    memcpy(output->data, digest, 64);
    output->length = 64;
    return 0;
}

static inline int64_t wysna_host_crypto_md5(void* input_handle, void* output_handle) {
    WysnaHostEntry* input = wysna_host_find(input_handle, 1);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!input || !output) return -1;

    uint8_t digest[16];
    wysna_md5_compute((const uint8_t*)input->data, input->length, digest);

    if (output->capacity < 16) {
        free(output->data);
        output->data = wysna_alloc_safe(16);
        output->capacity = 16;
    }
    memcpy(output->data, digest, 16);
    output->length = 16;
    return 0;
}

static inline int64_t wysna_host_crypto_random_bytes(void* output_handle, int64_t count) {
    if (count <= 0 || count > (1024 * 1024 * 64)) return -1;
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;

    if (output->capacity < (size_t)count) {
        free(output->data);
        output->data = wysna_alloc_safe((size_t)count);
        output->capacity = (size_t)count;
    }
    if (wysna_crypto_fill_random((uint8_t*)output->data, (size_t)count) != 0) {
        return -1;
    }
    output->length = (size_t)count;
    return 0;
}

static inline int64_t wysna_host_crypto_constant_time_compare(void* a_handle, void* b_handle) {
    WysnaHostEntry* a = wysna_host_find(a_handle, 1);
    WysnaHostEntry* b = wysna_host_find(b_handle, 1);
    if (!a || !b) return -1;
    if (a->length != b->length) return 0;

    const uint8_t* p1 = (const uint8_t*)a->data;
    const uint8_t* p2 = (const uint8_t*)b->data;
    volatile uint8_t diff = 0;
    for (size_t i = 0; i < a->length; i++) {
        diff |= (p1[i] ^ p2[i]);
    }
    return (diff == 0) ? 1 : 0;
}

#endif /* WYSNA_HOST_CRYPTO_H */
