//! Compile-time scaling regressions. Dataflow over one long function once
//! revisited every join per path and kept dense per-local states in every
//! block (ranges, moves, borrows): 200 branches took 28 s, 500 over ten
//! minutes, and 2000 needed over a gigabyte. Worklists now follow reverse
//! postorder and states are sparse.

use std::time::{Duration, Instant};

#[test]
fn long_branchy_function_checks_in_near_linear_time() {
    let mut src = String::from("fn main() {\n    var s: i64 = 0\n");
    for i in 0..2000 {
        src.push_str(&format!("    s = s + {i}\n    if s > 1000000 {{\n        s = 0\n    }}\n"));
    }
    src.push_str("    print(s)\n}\n");
    let dir = std::env::temp_dir().join(format!("tarn-scaling-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("main.tarn");
    std::fs::write(&file, src).unwrap();
    let start = Instant::now();
    let res = tarn_driver::check(&file).unwrap();
    let elapsed = start.elapsed();
    std::fs::remove_dir_all(&dir).unwrap();
    assert!(!res.has_errors());
    // Generous for unoptimized test builds; the regression took minutes.
    assert!(elapsed < Duration::from_secs(30), "checking took {elapsed:?}");
}
