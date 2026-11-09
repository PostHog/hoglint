//! A plain `cargo build` of the extension needs the link flags maturin passes
//! (`-undefined dynamic_lookup` on macOS): the interpreter provides the Python symbols.

fn main() {
    pyo3_build_config::add_extension_module_link_args();
}
