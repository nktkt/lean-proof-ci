namespace Broken

/-- Compiles with a warning but depends on sorryAx. -/
theorem unfinished : False := by sorry

/-- No sorry in this declaration's source; it inherits the missing proof. -/
theorem indirect : 1 = 2 := False.elim unfinished

/-- A made-up assumption is not an approved foundational axiom. -/
axiom invented : False

theorem assumed : 2 = 3 := False.elim invented

end Broken
