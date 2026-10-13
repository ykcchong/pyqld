/* Native DBF row conversion. Header parsing and the reference implementation
 * live in dbf.py. Keep Python's numeric conversions for identical semantics. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

typedef struct {
    PyObject *key;  /* borrowed from the argument list */
    Py_ssize_t start, end;
    int type, decimals;
} Field;

static PyObject *
cell_value(const char *data, Py_ssize_t size, int type, int decimals)
{
    PyObject *text, *value;
    while (size && (*data == ' ' || *data == '\0')) {
        ++data;
        --size;
    }
    while (size && (data[size - 1] == ' ' || data[size - 1] == '\0'))
        --size;
    if (type == 'L')
        return PyBool_FromLong(size == 1 &&
            (*data == 'T' || *data == 't' || *data == 'Y' ||
             *data == 'y' || *data == '1'));
    if (type == 'N' && size == 0)
        Py_RETURN_NONE;
    text = PyUnicode_DecodeASCII(data, size, "replace");
    if (text == NULL || type != 'N')
        return text;
    value = decimals ? PyFloat_FromString(text) : PyLong_FromUnicodeObject(text, 10);
    if (value == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) {
        PyErr_Clear();
        value = PyFloat_FromString(text);
    }
    if (value == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) {
        PyErr_Clear();
        return text;
    }
    Py_DECREF(text);
    return value;
}

static PyObject *
parse_records(PyObject *self, PyObject *args)
{
    PyObject *input, *layout, *snapshot = NULL, *rows = NULL, *row = NULL;
    Field *fields = NULL;
    Py_ssize_t offset, stride, count, nfields, n, i, j;
    const char *data;
    (void)self;
    if (!PyArg_ParseTuple(args, "O!nnnO!:parse_records", &PyBytes_Type, &input,
                          &offset, &stride, &count, &PyList_Type, &layout))
        return NULL;
    n = PyBytes_GET_SIZE(input);
    if (offset < 0 || offset > n || stride <= 0 || count < 0 ||
        count > (n - offset) / stride) {
        PyErr_SetString(PyExc_ValueError, "invalid record bounds");
        return NULL;
    }
    snapshot = PyList_AsTuple(layout);
    if (snapshot == NULL)
        return NULL;
    nfields = PyTuple_GET_SIZE(snapshot);
    fields = PyMem_Calloc((size_t)(nfields ? nfields : 1), sizeof(Field));
    if (fields == NULL) {
        Py_DECREF(snapshot);
        return PyErr_NoMemory();
    }
    for (j = 0; j < nfields; ++j) {
        PyObject *kind, *entry = PyTuple_GET_ITEM(snapshot, j);
        Field *f = fields + j;
        if (!PyTuple_Check(entry)) {
            PyErr_SetString(PyExc_TypeError, "field layout entries must be tuples");
            goto error;
        }
        if (!PyArg_ParseTuple(entry, "UnnUi", &f->key, &f->start, &f->end,
                              &kind, &f->decimals))
            goto error;
        if (f->start < 0 || f->end < f->start || PyUnicode_GET_LENGTH(kind) != 1) {
            PyErr_SetString(PyExc_ValueError, "invalid field layout");
            goto error;
        }
        f->type = (int)PyUnicode_ReadChar(kind, 0);
        if (f->type != 'C' && f->type != 'N' && f->type != 'L') {
            PyErr_SetString(PyExc_ValueError, "invalid field type");
            goto error;
        }
        /* Match Python slices even if a header's record size omits the
         * deletion byte, or a field extends beyond the record. */
        if (f->start > stride) f->start = stride;
        if (f->end > stride) f->end = stride;
    }
    rows = PyList_New(count);
    if (rows == NULL)
        goto error;
    data = PyBytes_AS_STRING(input) + offset;
    for (i = 0; i < count; ++i, data += stride) {
        row = PyDict_New();
        if (row == NULL)
            goto error;
        if (*data == '*' && PyDict_SetItemString(row, "__deleted__", Py_True) < 0)
            goto error;
        for (j = 0; j < nfields; ++j) {
            Field *f = fields + j;
            PyObject *value = cell_value(data + f->start, f->end - f->start,
                                         f->type, f->decimals);
            int status;
            if (value == NULL)
                goto error;
            status = PyDict_SetItem(row, f->key, value);
            Py_DECREF(value);
            if (status < 0)
                goto error;
        }
        PyList_SET_ITEM(rows, i, row);
        row = NULL;
    }
    PyMem_Free(fields);
    Py_DECREF(snapshot);
    return rows;
error:
    Py_XDECREF(row);
    Py_XDECREF(rows);
    PyMem_Free(fields);
    Py_XDECREF(snapshot);
    return NULL;
}

static PyMethodDef methods[] = {
    {"parse_records", parse_records, METH_VARARGS, "Convert DBF rows using a validated layout."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_dbf_native", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};
PyMODINIT_FUNC
PyInit__dbf_native(void)
{
    return PyModule_Create(&module);
}
