/* Waters' custom LZSS format. Keep semantics in sync with codec.py.
 * XOR is applied only to literals: copying decrypted back-references is
 * equivalent to decrypting the complete output afterwards.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

static uint32_t
read_u32(const unsigned char *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int
reserve(unsigned char **out, Py_ssize_t *capacity, Py_ssize_t used,
        Py_ssize_t extra)
{
    Py_ssize_t needed, grown;
    unsigned char *p;
    if (extra > PY_SSIZE_T_MAX - used) {
        PyErr_NoMemory();
        return -1;
    }
    needed = used + extra;
    if (needed <= *capacity)
        return 0;
    grown = *capacity > PY_SSIZE_T_MAX / 2 ? PY_SSIZE_T_MAX : *capacity * 2;
    if (grown < 4096)
        grown = 4096;
    if (grown < needed)
        grown = needed;
    p = PyMem_Realloc(*out, (size_t)grown);
    if (p == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    *out = p;
    *capacity = grown;
    return 0;
}

static PyObject *
decode(PyObject *self, PyObject *args)
{
    PyObject *input, *result;
    const unsigned char *data;
    unsigned char *out = NULL;
    Py_ssize_t n, off = 0, used = 0, capacity = 0;
    unsigned int key;
    (void)self;
    if (!PyArg_ParseTuple(args, "O!I:decode", &PyBytes_Type, &input, &key))
        return NULL;
    if (key > 255) {
        PyErr_SetString(PyExc_ValueError, "XOR key must be between 0 and 255");
        return NULL;
    }
    data = (const unsigned char *)PyBytes_AS_STRING(input);
    n = PyBytes_GET_SIZE(input);
    while (n - off >= 4) {
        uint32_t flags_size = read_u32(data + off), tokens_size;
        Py_ssize_t flags_off, tokens_off, end, tp, fi;
        off += 4;
        /* Subtraction avoids overflow even on 32-bit platforms. */
        if (flags_size == 0 || (uint64_t)flags_size + 4 > (uint64_t)(n - off))
            break;
        flags_off = off;
        off += flags_size;
        tokens_size = read_u32(data + off);
        off += 4;
        if ((uint64_t)tokens_size > (uint64_t)(n - off))
            break;
        tokens_off = off;
        end = off + tokens_size;
        tp = tokens_off;
        for (fi = 0; fi < (Py_ssize_t)flags_size && tp < end; ++fi) {
            unsigned int flags = data[flags_off + fi], shift;
            for (shift = 0; shift < 8; shift += 2) {
                unsigned int code = (flags >> shift) & 3;
                Py_ssize_t distance, length, src, k;
                if (code == 0) {
                    if (tp == end)
                        goto block_done;
                    if (reserve(&out, &capacity, used, 1) < 0)
                        goto error;
                    out[used++] = data[tp++] ^ key;
                    continue;
                }
                if (end - tp < (code == 2 ? 3 : 2))
                    goto block_done;
                distance = data[tp++];
                if (code == 2)
                    distance |= (Py_ssize_t)data[tp++] << 8;
                length = data[tp++];
                /* The reference clamps excessive distances to output start
                 * and emits nothing for distance zero or empty history. */
                src = distance > used ? 0 : used - distance;
                if (src == used || length == 0)
                    continue;
                if (reserve(&out, &capacity, used, length) < 0)
                    goto error;
                if (length <= used - src) {
                    memcpy(out + used, out + src, (size_t)length);
                    used += length;
                } else {
                    /* Forward copying is required for repeating references;
                     * memmove would not reproduce LZSS overlap semantics. */
                    for (k = 0; k < length; ++k) {
                        out[used] = out[src + k];
                        ++used;
                    }
                }
            }
        }
block_done:
        off = end;
    }
    result = PyBytes_FromStringAndSize((const char *)out, used);
    PyMem_Free(out);
    return result;
error:
    PyMem_Free(out);
    return NULL;
}

static PyMethodDef methods[] = {
    {"decode", decode, METH_VARARGS, "Decode LZSS with a fused XOR key."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_codec_native", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__codec_native(void)
{
    return PyModule_Create(&module);
}
