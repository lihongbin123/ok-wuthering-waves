/* Native USER32 window-class rename hooks.
 *
 * The previous implementation installed ctypes callbacks into the USER32 IAT,
 * so every window registration/creation performed by a Qt thread had to acquire
 * the Python GIL.  The GUI thread holds the GIL while running QWidget paint
 * code, which deadlocked against those callbacks ("Python 未响应").
 *
 * Here the same rules run natively; Python only locates the IAT slots and
 * points them at the exported hooks below.  No interpreter is involved at
 * runtime.
 *
 * Rules mirror rebrand/brand.py::Brand.rename_class exactly.
 */

#include <windows.h>

#define EXPORT __declspec(dllexport)
#define NAME_CAP 512

EXPORT const char kl_wnd_version[] = "1.0.0-native";

static WCHAR g_prefix[64] = L"Boreas";
static WCHAR g_product[128] = L"KL";
static WCHAR g_overlay[128] = L"Boreas.OverlaySurface";

typedef ATOM (WINAPI *PFN_RegisterClassExW)(const WNDCLASSEXW *);
typedef ATOM (WINAPI *PFN_RegisterClassW)(const WNDCLASSW *);
typedef BOOL (WINAPI *PFN_UnregisterClassW)(LPCWSTR, HINSTANCE);
typedef HWND (WINAPI *PFN_CreateWindowExW)(DWORD, LPCWSTR, LPCWSTR, DWORD, int, int,
                                           int, int, HWND, HMENU, HINSTANCE, LPVOID);
typedef HWND (WINAPI *PFN_CreateWindowExA)(DWORD, LPCSTR, LPCSTR, DWORD, int, int,
                                           int, int, HWND, HMENU, HINSTANCE, LPVOID);

static PFN_RegisterClassExW g_orig_registerclassexw;
static PFN_RegisterClassW   g_orig_registerclassw;
static PFN_UnregisterClassW g_orig_unregisterclassw;
static PFN_CreateWindowExW  g_orig_createwindowexw;
static PFN_CreateWindowExA  g_orig_createwindowexa;

/* ------------------------------------------------------------------ helpers */
static int w_len(const WCHAR *s) { int n = 0; if (!s) return 0; while (s[n]) n++; return n; }

static void w_copy(WCHAR *dst, const WCHAR *src, int cap) {
    int i = 0;
    if (!dst || cap <= 0) return;
    if (src) { while (src[i] && i < cap - 1) { dst[i] = src[i]; i++; } }
    dst[i] = 0;
}

static void w_cat(WCHAR *dst, const WCHAR *src, int cap) {
    int n = w_len(dst);
    if (n >= cap - 1) return;
    w_copy(dst + n, src, cap - n);
}

static int w_eq(const WCHAR *a, const WCHAR *b) {
    int i = 0;
    if (!a || !b) return 0;
    while (a[i] && b[i]) { if (a[i] != b[i]) return 0; i++; }
    return a[i] == b[i];
}

static WCHAR w_lower(WCHAR c) { return (c >= L'A' && c <= L'Z') ? (WCHAR)(c + 32) : c; }

static int starts_ci(const WCHAR *s, const WCHAR *prefix) {
    int i = 0;
    if (!s || !prefix) return 0;
    while (prefix[i]) {
        if (!s[i] || w_lower(s[i]) != w_lower(prefix[i])) return 0;
        i++;
    }
    return 1;
}

/* --------------------------------------------------------------------- SHA1 */
#define ROL32(x, n) (((x) << (n)) | ((x) >> (32 - (n))))

static void sha1_compress(unsigned int h[5], const unsigned char *p) {
    unsigned int w[80];
    int i;
    for (i = 0; i < 16; i++) {
        w[i] = ((unsigned int)p[i * 4] << 24) | ((unsigned int)p[i * 4 + 1] << 16) |
               ((unsigned int)p[i * 4 + 2] << 8) | (unsigned int)p[i * 4 + 3];
    }
    for (i = 16; i < 80; i++) {
        w[i] = ROL32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    }
    {
        unsigned int a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
        for (i = 0; i < 80; i++) {
            unsigned int f, k, t;
            if (i < 20)      { f = (b & c) | ((~b) & d);      k = 0x5A827999u; }
            else if (i < 40) { f = b ^ c ^ d;                 k = 0x6ED9EBA1u; }
            else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDCu; }
            else             { f = b ^ c ^ d;                 k = 0xCA62C1D6u; }
            t = ROL32(a, 5) + f + e + k + w[i];
            e = d; d = c; c = ROL32(b, 30); b = a; a = t;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
    }
}

/* First six lowercase hex chars of sha1(data) - matches hashlib.sha1().hexdigest()[:6]. */
static void sha1_hex6(const unsigned char *data, int len, WCHAR out[7]) {
    unsigned int h[5] = {0x67452301u, 0xEFCDAB89u, 0x98BADCFEu, 0x10325476u, 0xC3D2E1F0u};
    unsigned char buf[128];
    unsigned long long bitlen = (unsigned long long)len * 8ULL;
    int blocklen, i;
    static const WCHAR hex[] = L"0123456789abcdef";

    while (len >= 64) {
        sha1_compress(h, data);
        data += 64;
        len -= 64;
    }
    for (i = 0; i < len; i++) buf[i] = data[i];
    buf[len] = 0x80;
    blocklen = (len + 1 <= 56) ? 64 : 128;
    for (i = len + 1; i < blocklen; i++) buf[i] = 0;
    for (i = 0; i < 8; i++) {
        buf[blocklen - 1 - i] = (unsigned char)((bitlen >> (8 * i)) & 0xFF);
    }
    sha1_compress(h, buf);
    if (blocklen == 128) sha1_compress(h, buf + 64);

    out[0] = hex[(h[0] >> 28) & 0xF];
    out[1] = hex[(h[0] >> 24) & 0xF];
    out[2] = hex[(h[0] >> 20) & 0xF];
    out[3] = hex[(h[0] >> 16) & 0xF];
    out[4] = hex[(h[0] >> 12) & 0xF];
    out[5] = hex[(h[0] >> 8) & 0xF];
    out[6] = 0;
}

/* ------------------------------------------------------------------ renaming */
static int rename_class(const WCHAR *name, WCHAR *out, int cap) {
    if (!name || !*name || cap < 2) return 0;

    if (w_eq(name, L"Boreas.OverlaySurface") || w_eq(name, L"Boreas.OverlaySurface")) {
        w_copy(out, g_overlay, cap);
        return 1;
    }

    if (name[0] == L'Q' && name[1] == L't') {
        int i = 2;
        while (name[i] >= L'0' && name[i] <= L'9') i++;
        if (i > 2) {
            const WCHAR *kind = name + i;
            WCHAR tail[64];
            if (w_eq(kind, L"QWindowIcon"))              w_copy(tail, L"WndSurface", 64);
            else if (w_eq(kind, L"QWindowToolSaveBits")) w_copy(tail, L"WndTool", 64);
            else if (w_eq(kind, L"QWindowOwnDC"))        w_copy(tail, L"WndDC", 64);
            else if (w_eq(kind, L"QWindow"))             w_copy(tail, L"Wnd", 64);
            else {
                char utf8[256];
                WCHAR hex6[7];
                int n = WideCharToMultiByte(CP_UTF8, 0, kind, -1, utf8, (int)sizeof(utf8), NULL, NULL);
                if (n > 0) sha1_hex6((const unsigned char *)utf8, n - 1, hex6);
                else        w_copy(hex6, L"000000", 7);
                w_copy(tail, L"Wnd", 64);
                w_cat(tail, hex6, 64);
            }
            w_copy(out, g_prefix, cap);
            {
                WCHAR digits[16];
                int d = 0, j;
                for (j = 2; j < i && d < 15; j++) digits[d++] = name[j];
                digits[d] = 0;
                w_cat(out, digits, cap);
            }
            w_cat(out, tail, cap);
            return 1;
        }
    }

    if (name[0] == L'_' && name[1] == L'q' && name[2] == L'_') {
        WCHAR low[64];
        int i;
        for (i = 0; g_prefix[i] && i < 63; i++) low[i] = w_lower(g_prefix[i]);
        low[i] = 0;
        w_copy(out, L"_", cap);
        w_cat(out, low, cap);
        w_cat(out, L"_", cap);
        w_cat(out, name + 3, cap);
        return 1;
    }

    if (starts_ci(name, L"kl-script")) {
        w_copy(out, g_prefix, cap);
        w_cat(out, name + 9, cap);
        return 1;
    }

    if (w_eq(name, L"KL") || w_eq(name, L"kl")) {
        w_copy(out, g_product, cap);
        return 1;
    }

    return 0;
}

/* --------------------------------------------------------------- public API */
EXPORT void WINAPI kl_wnd_init(const WCHAR *prefix, const WCHAR *product, const WCHAR *overlay) {
    if (prefix)  w_copy(g_prefix, prefix, 64);
    if (product) w_copy(g_product, product, 128);
    if (overlay) w_copy(g_overlay, overlay, 128);
}

EXPORT void WINAPI kl_wnd_set_originals(void *registerclassexw, void *registerclassw,
                                         void *unregisterclassw, void *createwindowexw,
                                         void *createwindowexa) {
    g_orig_registerclassexw = (PFN_RegisterClassExW)registerclassexw;
    g_orig_registerclassw   = (PFN_RegisterClassW)registerclassw;
    g_orig_unregisterclassw = (PFN_UnregisterClassW)unregisterclassw;
    g_orig_createwindowexw  = (PFN_CreateWindowExW)createwindowexw;
    g_orig_createwindowexa  = (PFN_CreateWindowExA)createwindowexa;
}

/* Exposed for tests: returns 1 when the name was remapped. */
EXPORT int WINAPI kl_wnd_rename(const WCHAR *name, WCHAR *out, int cap) {
    return rename_class(name, out, cap);
}

EXPORT ATOM WINAPI kl_RegisterClassExW(const WNDCLASSEXW *wc) {
    if (!g_orig_registerclassexw) return 0;
    if (!wc) return g_orig_registerclassexw(wc);
    {
        WCHAR buf[NAME_CAP];
        if (rename_class(wc->lpszClassName, buf, NAME_CAP)) {
            WNDCLASSEXW copy = *wc;
            copy.lpszClassName = buf;
            return g_orig_registerclassexw(&copy);
        }
    }
    return g_orig_registerclassexw(wc);
}

EXPORT ATOM WINAPI kl_RegisterClassW(const WNDCLASSW *wc) {
    if (!g_orig_registerclassw) return 0;
    if (!wc) return g_orig_registerclassw(wc);
    {
        WCHAR buf[NAME_CAP];
        if (rename_class(wc->lpszClassName, buf, NAME_CAP)) {
            WNDCLASSW copy = *wc;
            copy.lpszClassName = buf;
            return g_orig_registerclassw(&copy);
        }
    }
    return g_orig_registerclassw(wc);
}

EXPORT BOOL WINAPI kl_UnregisterClassW(LPCWSTR class_name, HINSTANCE instance) {
    WCHAR buf[NAME_CAP];
    if (!g_orig_unregisterclassw) return FALSE;
    if (class_name && (ULONG_PTR)class_name > 0xFFFF &&
        rename_class(class_name, buf, NAME_CAP)) {
        class_name = buf;
    }
    return g_orig_unregisterclassw(class_name, instance);
}

EXPORT HWND WINAPI kl_CreateWindowExW(DWORD ex_style, LPCWSTR class_name, LPCWSTR window_name,
                                       DWORD style, int x, int y, int w, int h,
                                       HWND parent, HMENU menu, HINSTANCE instance, LPVOID param) {
    WCHAR buf[NAME_CAP];
    if (!g_orig_createwindowexw) return NULL;
    if (class_name && (ULONG_PTR)class_name > 0xFFFF &&
        rename_class(class_name, buf, NAME_CAP)) {
        class_name = buf;
    }
    return g_orig_createwindowexw(ex_style, class_name, window_name, style, x, y, w, h,
                                  parent, menu, instance, param);
}

EXPORT HWND WINAPI kl_CreateWindowExA(DWORD ex_style, LPCSTR class_name, LPCSTR window_name,
                                       DWORD style, int x, int y, int w, int h,
                                       HWND parent, HMENU menu, HINSTANCE instance, LPVOID param) {
    WCHAR wide[NAME_CAP];
    char narrow[NAME_CAP * 2];
    if (!g_orig_createwindowexa) return NULL;
    if (class_name && (ULONG_PTR)class_name > 0xFFFF) {
        int n = MultiByteToWideChar(CP_ACP, 0, class_name, -1, wide, NAME_CAP);
        if (n > 0) {
            WCHAR renamed[NAME_CAP];
            if (rename_class(wide, renamed, NAME_CAP) &&
                WideCharToMultiByte(CP_ACP, 0, renamed, -1, narrow, (int)sizeof(narrow), NULL, NULL) > 0) {
                class_name = narrow;
            }
        }
    }
    return g_orig_createwindowexa(ex_style, class_name, window_name, style, x, y, w, h,
                                  parent, menu, instance, param);
}

BOOL WINAPI DllMain(HINSTANCE self, DWORD reason, LPVOID reserved) {
    (void)self; (void)reason; (void)reserved;
    return TRUE;
}
