mod ledger;

use std::{env, process};

fn main() {
    let size = env::args().nth(1).and_then(|arg| arg.parse::<i64>().ok());
    let Some(size) = size.filter(|size| (1..=1000000).contains(size)) else {
        process::exit(2);
    };
    let result = ledger::exercise(size);
    println!("{} {} {}", result.count, result.total, result.rolled_back);
}
