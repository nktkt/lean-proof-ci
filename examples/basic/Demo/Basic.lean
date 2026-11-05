namespace Demo

/-- A definition whose body is tracked separately from theorem statements. -/
def twice (n : Nat) : Nat := n + n

theorem twice_eq_add (n : Nat) : twice n = n + n := by
  rfl

theorem add_zero (n : Nat) : n + 0 = n := by
  rfl

/-- Changing this proof alone should not change the statement fingerprint. -/
theorem add_comm (n m : Nat) : n + m = m + n := by
  exact Nat.add_comm n m

end Demo
