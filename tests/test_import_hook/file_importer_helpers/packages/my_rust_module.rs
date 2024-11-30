use pyo3::prelude::*;

#[pyfunction]
pub fn do_something(a: usize, b: usize) -> PyResult<usize> {
    Ok(a + b)
}

#[pymodule]
pub fn my_rust_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(do_something, m)?)?;
    Ok(())
}
