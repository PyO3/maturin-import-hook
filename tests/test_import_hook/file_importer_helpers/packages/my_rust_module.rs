use pyo3::prelude::*;

#[pyfunction]
pub fn do_something(a: usize, b: usize) -> PyResult<usize> {
    Ok(a + b)
}

#[pymodule]
pub fn my_rust_module(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_wrapped(wrap_pyfunction!(do_something))?;
    Ok(())
}
