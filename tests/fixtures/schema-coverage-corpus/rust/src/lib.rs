//! WI-luzuh fixture: Rust source-language constructs.
//! Triggers: struct, trait, impl, enum, function, type alias, const.

pub struct MyStruct {
    pub field: i32,
}

pub trait MyTrait {
    fn method(&self) -> i32;
}

impl MyTrait for MyStruct {
    fn method(&self) -> i32 {
        self.field
    }
}

pub enum MyEnum {
    Variant1,
    Variant2(i32),
}

pub type MyAlias = i32;

pub const MY_CONST: i32 = 42;

pub fn my_function(x: i32) -> i32 {
    x + 1
}

use std::collections::HashMap;
