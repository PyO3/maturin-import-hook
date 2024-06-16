use pyo3::prelude::*;

#[pymodule]
fn blank_project(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
