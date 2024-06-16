use pyo3::prelude::*;

#[pyfunction]
fn get_num() -> usize {
    if cfg!(feature = "large_number") {
        100
    } else {
        let num = 10;
        num
    }
}

#[pymodule]
fn my_project(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_wrapped(wrap_pyfunction!(get_num))?;
    Ok(())
}
