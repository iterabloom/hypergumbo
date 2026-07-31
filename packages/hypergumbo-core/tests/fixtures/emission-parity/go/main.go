// Emission-parity go fixture.
// See tests/fixtures/emission-parity/README.md.
package main

import "fmt"

// MaxItems caps the number of items processed.
var MaxItems = 5

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
	if total > MaxItems {
		total = MaxItems
	}
	return helper(total)
}

// Service is a small service.
type Service struct {
	count int
}

// Run runs the service.
func (s Service) Run() string {
	return Process([]int{1, 2, 3}, true)
}

// Drawable is an abstract type whose member signatures are container members.
type Drawable interface {
	Draw() string
	Area() float64
}

func main() {
	Service{}.Run()
}
