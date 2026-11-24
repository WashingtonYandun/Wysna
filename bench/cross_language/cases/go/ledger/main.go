package main

import (
	"fmt"
	"os"
	"strconv"

	"benchmark.local/ledger/engine"
)

func main() {
	size, err := strconv.Atoi(os.Args[1])
	if err != nil || size < 1 || size > 1000000 {
		os.Exit(2)
	}
	result := engine.Exercise(size)
	fmt.Println(result.Count, result.Total, result.RolledBack)
}
