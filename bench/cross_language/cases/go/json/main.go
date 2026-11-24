package main

import (
	"encoding/json"
	"fmt"
	"os"
)

type record struct {
	ID     *int  `json:"id"`
	Units  *int  `json:"units"`
	Active *bool `json:"active"`
}

func main() {
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		os.Exit(2)
	}
	var records []record
	if json.Unmarshal(raw, &records) != nil || records == nil {
		os.Exit(2)
	}
	accepted, rejected, total := 0, 0, 0
	for _, item := range records {
		if item.ID == nil || item.Units == nil || item.Active == nil {
			os.Exit(2)
		}
		if *item.ID > 0 && *item.Active && *item.Units >= 0 && *item.Units <= 1000 {
			accepted++
			total += *item.Units
		} else {
			rejected++
		}
	}
	fmt.Println(accepted, rejected, total)
}
