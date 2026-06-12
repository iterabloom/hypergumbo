// Emission-parity go fixture.
// See tests/fixtures/emission-parity/README.md.
package main

import "fmt"

func helper(value int) string {
	return fmt.Sprintf("%d", value)
}

// Process processes items with branching.
func Process(items []int, flag bool) string {
	total := 0
	if flag {
		total += 1
	}
	if len(items) > 0 {
		total += len(items)
	}
	if total > 5 {
		total = 5
	}
	return helper(total)
}

// Service is a small service.
type Service struct{}

// Run runs the service.
func (s Service) Run() string {
	return Process([]int{1, 2, 3}, true)
}

func main() {
	Service{}.Run()
}
