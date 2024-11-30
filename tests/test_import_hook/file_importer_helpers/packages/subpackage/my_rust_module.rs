use pyo3::prelude::*;

#[pyfunction]
pub fn get_num() -> PyResult<usize> {
    Ok(42)
}

#[pymodule]
pub fn my_rust_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_num, m)?)?;
    Ok(())
}
