//! WI-luzuh fixture: Rust source-language constructs.
//! Triggers: struct, trait, impl, enum, function, type alias, const,
//! module-attribute read.

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

/// A GENUINE module-attribute read, which is what `module_attr_ref` is for.
///
/// INV-pusin: until that fix, this corpus covered `Edge.type=module_attr_ref`
/// and `Edge.evidence_type=module_attribute_reference` ONLY through the `use`
/// path above — which the analyzer emitted as an attribute read by mistake.
/// The coverage was real and the thing it covered was a bug, so removing the
/// bug removed the coverage. `std::env::consts::OS` is the shape those values
/// are supposed to describe: an attribute the I/O catalogue can classify
/// (`module: std::env, attributes: [consts]` -> env_read).
pub fn platform() -> &'static str {
    std::env::consts::OS
}
