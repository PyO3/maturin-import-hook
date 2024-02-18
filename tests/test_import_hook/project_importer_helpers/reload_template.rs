use pyo3::prelude::*;
use pyo3::class::basic::CompareOp;
use pyo3::types::PyDict;

#[pyfunction]
fn get_num() -> usize {
    let num = 10;
    num
}

#[pyclass]
struct Integer {
    value: i32,
    name: String,
}

#[pymethods]
impl Integer {
    #[new]
    fn new(value: i32, name: &str) -> Self {
        Integer { value, name: name.to_string() }
    }

    fn __richcmp__(&self, other: &Self, op: CompareOp, py: Python<'_>) -> PyResult<bool> {
        let logging = PyModule::import(py, "logging")?;
        let message = format!("comparing Integer instances {} and {}", self.name, other.name);
        logging.getattr("info")?.call1((&message,))?;
        Ok(op.matches(self.value.cmp(&other.value)))
    }
}

#[pymodule]
fn my_project(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_wrapped(wrap_pyfunction!(get_num))?;
    m.add_class::<Integer>()?;

    let data = PyDict::new(py);
    data.set_item("foo", 123)?;
    m.add("data", data)?;

    if !m.hasattr("data_init_once")? {
        let data = PyDict::new(py);
        data.set_item("foo", 123)?;
        m.add("data_init_once", data)?;
    }

    m.add("data_str", "foo")?;

    let logging = PyModule::import(py, "logging")?;
    logging.getattr("info")?.call1(("my_project extension module initialised",))?;

    Ok(())
}
