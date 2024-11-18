use pyo3::prelude::*;

#[pyfunction]
fn get_num() -> usize { 10 }

#[pymodule]
fn my_script(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_num, m)?)?;
    Ok(())
}
