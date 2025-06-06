use anyhow::Result;
use maturin::BuildOptions;
use serde_json::{json, Value};
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

struct TemporaryChdir {
    old_dir: PathBuf,
}

impl TemporaryChdir {
    pub fn chdir(new_cwd: &Path) -> std::io::Result<Self> {
        let old_dir = env::current_dir()?;
        match env::set_current_dir(new_cwd) {
            Ok(()) => Ok(Self { old_dir }),
            Err(e) => Err(e),
        }
    }
}

impl Drop for TemporaryChdir {
    fn drop(&mut self) {
        env::set_current_dir(&self.old_dir).unwrap();
    }
}

fn resolve_package(project_root: &Path) -> Result<Value> {
    let project_root = project_root.canonicalize()?;
    let _cwd = TemporaryChdir::chdir(&project_root)?;

    let build_options: BuildOptions = Default::default();
    let build_context = build_options.into_build_context().build()?;
    let extension_module_dir = if build_context.project_layout.python_module.is_some() {
        Some(relative_path(
            &build_context.project_layout.rust_module,
            &project_root,
        )?)
    } else {
        None
    };
    let python_module = if let Some(p) = build_context.project_layout.python_module {
        Some(relative_path(&p, &project_root)?)
    } else {
        None
    };

    Ok(json!({
        "cargo_manifest_path": relative_path(&build_context.manifest_path, &project_root)?,
        "python_dir": relative_path(&build_context.project_layout.python_dir, &project_root)?,
        "python_module": python_module,
        "module_full_name": build_context.module_name,
        "extension_module_dir": extension_module_dir
    }))
}

fn relative_path(p: &Path, root: &Path) -> Result<PathBuf> {
    let rel = p.strip_prefix(root)?;
    if rel.as_os_str().is_empty() {
        Ok(PathBuf::from("."))
    } else {
        Ok(rel.to_owned())
    }
}

fn resolve_all_packages(test_crates_dir: &Path) -> Result<Value> {
    let mut resolved_packages = serde_json::Map::new();
    let mut entries = fs::read_dir(test_crates_dir)?
        .map(|res| res.map(|e| e.path()))
        .collect::<std::result::Result<Vec<PathBuf>, std::io::Error>>()?;
    entries.sort();
    for path in entries {
        if path.join("pyproject.toml").exists() {
            let project_name = path.file_name().unwrap().to_str().unwrap().to_owned();
            println!("resolve '{}'", project_name);
            match resolve_package(&path) {
                Ok(value) => {
                    resolved_packages.insert(project_name, value);
                }
                Err(err) => {
                    println!("resolve failed with: {:?}", err);
                    resolved_packages.insert(project_name, Value::Null);
                }
            }
        }
    }
    Ok(Value::Object(resolved_packages))
}

fn get_git_hash(repo_path: &Path) -> Result<String> {
    let output = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo_path)
        .output()?;
    Ok(String::from_utf8(output.stdout)?.trim_end().to_owned())
}

fn main() {
    let mut args = env::args();
    args.next();
    let maturin_dir = PathBuf::from(args.next().unwrap());
    let output_path = PathBuf::from(args.next().unwrap());
    let git_hash = get_git_hash(&maturin_dir).unwrap();
    let resolved = resolve_all_packages(&maturin_dir.join("test-crates")).unwrap();

    let mut root = serde_json::Map::new();
    root.insert("commit".to_owned(), Value::String(git_hash));
    root.insert("crates".to_owned(), resolved);
    let output = serde_json::to_string_pretty(&Value::Object(root)).unwrap();

    fs::write(&output_path, output).unwrap();

    println!("\n\nWRITTEN SUCCESSFULLY TO {:?}", output_path);
}
