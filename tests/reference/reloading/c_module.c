#define PY_SSIZE_T_CLEAN
#include <stdlib.h>
#include <assert.h>
#include <Python.h>


static PyObject* get_num(PyObject* self, PyObject* args) {
    int num = 10;
    return PyLong_FromLong(num);
}

static PyMethodDef c_module_methods[] = {
    {"get_num", get_num, METH_NOARGS, "get a number"},
    {NULL, NULL, 0, NULL}  // end sentinel
};

static struct PyModuleDef c_module = {
    PyModuleDef_HEAD_INIT,
    "c_module",  // name
    NULL,  // documentation
    -1,  // per-interpreter state size
    c_module_methods,
};

PyObject *create_dict() {
    PyObject *data = PyDict_New();
    assert(data != NULL);
    PyObject *key = PyUnicode_FromString("foo");
    assert(key != NULL);
    PyObject *val = PyLong_FromLong(123);
    int result = PyDict_SetItem(data, key, val);
    assert(result == 0);
    Py_DECREF(val);
    Py_DECREF(key);
    return data;
}

PyMODINIT_FUNC PyInit_c_module(void) {
    PyObject *logging_module = PyImport_ImportModule("logging");
    assert(logging_module != NULL);
    PyObject *info = PyObject_GetAttrString(logging_module, "info");
    assert(info != NULL);

    PyObject_CallObject(info, Py_BuildValue("(s)", "init c module"));

    PyObject *mod = PyModule_Create(&c_module);
    assert(mod != NULL);

    PyModule_AddObject(mod, "data", create_dict());

    if (!PyObject_HasAttrString(mod, "data_init_once")) {
        PyModule_AddObject(mod, "data_init_once", create_dict());
    }

    return mod;
}
