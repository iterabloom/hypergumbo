// WI-luzuh fixture: Go source-language constructs.
// Triggers: interface, struct, const, var, function, method.

package main

import "fmt"

type MyInterface interface {
	Run() error
}

type MyStruct struct {
	Field int
}

func (s *MyStruct) Run() error {
	fmt.Println(s.Field)
	return nil
}

const MyConst = 42

var MyVar = "hello"

func MyFunction(x int) int {
	return x + 1
}
