use pyo3::prelude::*;

#[pyfunction]
fn get_num() -> usize { 20 }

#[pyfunction]
fn get_other_num() -> usize { 100 }

#[pymodule(gil_used = false)]
fn my_script(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_num, m)?)?;
    m.add_function(wrap_pyfunction!(get_other_num, m)?)?;
    Ok(())
}
