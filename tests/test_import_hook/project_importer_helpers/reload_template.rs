use pyo3::class::basic::CompareOp;
use pyo3::prelude::*;
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
    fn new(value: i32, name: String) -> Self {
        Integer { value, name }
    }

    fn __richcmp__(&self, other: &Self, op: CompareOp, py: Python<'_>) -> PyResult<bool> {
        let logging = PyModule::import(py, "logging")?;
        let message = format!(
            "comparing Integer instances {} and {}",
            self.name, other.name
        );
        logging.getattr("info")?.call1((&message,))?;
        Ok(op.matches(self.value.cmp(&other.value)))
    }
}

// creating a separate class to ensure that the changes made to support pickling
// do not affect the test results
#[pyclass(module = "my_project")] // setting module required for pickling
struct PicklableInteger {
    value: i32,
    name: String,
}

#[pymethods]
impl PicklableInteger {
    #[new]
    fn new(value: i32, name: String) -> Self {
        PicklableInteger { value, name }
    }

    fn __richcmp__(&self, other: &Self, op: CompareOp, py: Python<'_>) -> PyResult<bool> {
        let logging = PyModule::import(py, "logging")?;
        let message = format!(
            "comparing PicklableInteger instances {} and {}",
            self.name, other.name
        );
        logging.getattr("info")?.call1((&message,))?;
        Ok(op.matches(self.value.cmp(&other.value)))
    }

    /// an alternative to implementing __getstate__ and __setstate__, the state returned
    /// from this method is fed into __new__ when unpickling.
    fn __getnewargs__(&self) -> PyResult<(i32, String)> {
        Ok((self.value, self.name.clone()))
    }
}

#[pymodule]
fn my_project(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_wrapped(wrap_pyfunction!(get_num))?;
    m.add_class::<Integer>()?;
    m.add_class::<PicklableInteger>()?;

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
    logging
        .getattr("info")?
        .call1(("my_project extension module initialised",))?;

    Ok(())
}
