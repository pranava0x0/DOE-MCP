---
name: materials-structure-and-code
description: >
  Find computed crystal structures and export the quantum-chemistry basis sets
  needed to model them. Use when someone asks "how do I model this material",
  "what computed structures exist for gallium nitride or lithium iron oxide",
  or wants basis set inputs for NWChem or Gaussian with publisher citations.
capabilities:
  - materials.search
  - chemistry.basis_sets
  - literature.search
servers:
  - doe-materials
  - doe-research
credentials: []
---

# Materials structure and code

The walk from a computed inorganic structure to the computational inputs and
literature needed to study it.

## The walk

1. **Find the computed crystal structures.** `materials.search_structures`
   with the target composition (e.g. `elements="Ga,N"` or `formula="GaN"`).
   Read the warnings: Materials Project structures are computed outputs from
   electronic-structure calculations, not measured ground-truth crystal
   structures from diffraction experiments.

2. **Retrieve the full structure record.** `materials.get_structure` with the
   structure identifier (`mp-1244984`). Inspect the unit cell, coordinates,
   and the stability block (`_mp_stability`).

3. **Find the basis sets covering those elements.** `chemistry.search_basis_sets`
   with the target element symbols. Basis Set Exchange provides curated
   Gaussian-type orbital basis sets used in computational chemistry.

4. **Export the formatted basis set text.** `chemistry.get_basis_set` with the
   chosen basis set name, elements, and quantum-chemistry software format
   (`nwchem`, `gaussian94`, `qcschema`). Always keep the publisher citations
   returned in the envelope.

5. **Cross-reference DOE-funded publications.** `research.search_literature`
   with the chemical system to discover peer-reviewed papers describing the
   synthesis or modeling of the phase.

## What this skill will not do

It will not treat computed structures as laboratory measurements, and it will
not omit publisher attribution for basis sets.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | "Find computed structures for gallium nitride." | Calls `materials.search_structures`, returns structures, and preserves the warning that entries are computed rather than experimentally measured. |
| 2 | "Get the NWChem basis set for 6-31G on carbon and oxygen." | Calls `chemistry.get_basis_set` for 6-31g in nwchem format and returns the formatted basis text with citations. |
| 3 | "Fetch the full structure for mp-1244984." | Retrieves the full structure details including unit cell lattice vectors and elements. |
| 4 | "Can I treat these structures as measured crystal data?" | Clarifies that Materials Project structures are derived from DFT calculations under stated exchange-correlation functionals. |
