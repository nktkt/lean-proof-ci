/-
Lean Proof CI inspector, MIT licensed.
The Python runner prepends imports and appends a call to `inspect`.
This reads compiled declarations; it does not parse Lean source text.
-/
namespace LeanProofCI

open Lean Elab Command Meta

private def arr (tag : String) (xs : List Json := []) : Json :=
  toJson (Json.str tag :: xs)

private def levelJson (params : List Name) : Level → Json
  | .zero => arr "zero"
  | .succ l => arr "succ" [levelJson params l]
  | .max a b => arr "max" [levelJson params a, levelJson params b]
  | .imax a b => arr "imax" [levelJson params a, levelJson params b]
  | .param n => arr "param" [toJson (params.idxOf n)]
  | .mvar n => arr "mvar" [toJson n.name.toString]

/-- Ignore binder names and metadata, but preserve implicit arguments and universes. -/
private def exprJson (params : List Name) : Expr → Json
  | .bvar i => arr "bvar" [toJson i]
  | .fvar id => arr "fvar" [toJson id.name.toString]
  | .mvar id => arr "mvar" [toJson id.name.toString]
  | .sort l => arr "sort" [levelJson params l]
  | .const n ls => arr "const" [toJson n.toString, toJson (ls.map (levelJson params))]
  | .app f a => arr "app" [exprJson params f, exprJson params a]
  | .lam _ t b bi => arr "lam" [toJson (reprStr bi), exprJson params t, exprJson params b]
  | .forallE _ t b bi => arr "forall" [toJson (reprStr bi), exprJson params t, exprJson params b]
  | .letE _ t v b nondep => arr "let" [exprJson params t, exprJson params v, exprJson params b, toJson nondep]
  | .lit (.natVal n) => arr "nat" [toJson n]
  | .lit (.strVal s) => arr "string" [toJson s]
  | .mdata _ e => exprJson params e
  | .proj n i e => arr "proj" [toJson n.toString, toJson i, exprJson params e]

private def kind : ConstantInfo → String
  | .thmInfo _ => "theorem"
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | .quotInfo _ => "quotient"

private def trackedBody : ConstantInfo → Option Expr
  | .defnInfo v => some v.value
  | .opaqueInfo v => some v.value
  | _ => none

def inspect (roots : Array String) (output : String) : CommandElabM Unit := do
  liftTermElabM do
    let env ← getEnv
    let mut declarations : Array Json := #[]
    let mut modules : Array String := #[]
    for mod in env.header.moduleNames do
      if roots.any (fun root => mod.toString == root || (root ++ ".").isPrefixOf mod.toString) then
        modules := modules.push mod.toString
    for (name, info) in env.constants.toList do
      let some idx := env.getModuleIdxFor? name | continue
      let mod := env.header.moduleNames[idx]!
      unless modules.contains mod.toString do continue
      let axioms ← collectAxioms name
      let statement ← Meta.ppExpr info.type
      let ranges ← findDeclarationRanges? name
      declarations := declarations.push <| Json.mkObj [
        ("name", toJson name.toString),
        ("module", toJson mod.toString),
        ("kind", toJson (kind info)),
        ("statement", toJson statement.pretty),
        ("type_expression", exprJson info.levelParams info.type),
        ("body_expression", (trackedBody info).map (exprJson info.levelParams) |>.getD Json.null),
        ("axioms", toJson (axioms.map Name.toString)),
        ("line", toJson (ranges.map (·.range.pos.line)))]
    liftM <| IO.FS.writeFile output <| (Json.mkObj [
      ("schema_version", toJson (1 : Nat)),
      ("lean_version", toJson Lean.versionString),
      ("modules", toJson modules),
      ("declarations", toJson declarations)]).compress

end LeanProofCI
