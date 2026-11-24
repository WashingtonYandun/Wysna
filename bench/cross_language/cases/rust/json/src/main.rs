use serde::Deserialize;
use std::{env, fs, process};

#[derive(Deserialize)]
struct Record {
    id: i64,
    units: i64,
    active: bool,
}

fn aggregate() -> Result<(usize, usize, i64), Box<dyn std::error::Error>> {
    let source = fs::read(env::args_os().nth(1).ok_or("missing path")?)?;
    let records: Vec<Record> = serde_json::from_slice(&source)?;
    let (mut accepted, mut rejected, mut total) = (0, 0, 0);
    for record in records {
        if record.id > 0 && record.active && (0..=1000).contains(&record.units) {
            accepted += 1;
            total += record.units;
        } else {
            rejected += 1;
        }
    }
    Ok((accepted, rejected, total))
}

fn main() {
    match aggregate() {
        Ok((accepted, rejected, total)) => println!("{accepted} {rejected} {total}"),
        Err(_) => process::exit(2),
    }
}
