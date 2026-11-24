package engine

type entry struct {
	id    int
	units int
}

type Summary struct {
	Count      int
	Total      int
	RolledBack bool
}

type ledger struct {
	balances map[int]int
	total    int
}

func (book *ledger) apply(entries []entry) bool {
	pending := make(map[int]int)
	subtotal := 0
	for _, item := range entries {
		if _, found := book.balances[item.id]; found {
			return false
		}
		if _, found := pending[item.id]; found {
			return false
		}
		pending[item.id] = item.units
		subtotal += item.units
	}
	for _, item := range entries {
		book.balances[item.id] = item.units
	}
	book.total += subtotal
	return true
}

func batch(start, size int, duplicate bool) []entry {
	var entries []entry
	for offset := 0; offset < size; offset++ {
		id := start + offset
		if duplicate && offset == size-1 {
			id = 1
		}
		entries = append(entries, entry{id, id%1000 + 1})
	}
	return entries
}

func Exercise(size int) Summary {
	book := ledger{map[int]int{0: 7}, 7}
	if !book.apply(batch(1, size, false)) {
		panic("commit failed")
	}
	rolledBack := !book.apply(batch(size+1, size, true))
	return Summary{len(book.balances), book.total, rolledBack}
}
