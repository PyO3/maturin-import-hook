use pyo3::prelude::*;

#[pymodule(gil_used = false)]
fn blank_project(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
