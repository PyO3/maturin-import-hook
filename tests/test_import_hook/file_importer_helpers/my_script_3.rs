use pyo3::prelude::*;

#[pyfunction]
fn get_num() -> usize {
    if cfg!(feature = "large_number") {
        100
    } else {
        10
    }
}

#[pymodule(gil_used = false)]
fn my_script(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_num, m)?)?;
    Ok(())
}
