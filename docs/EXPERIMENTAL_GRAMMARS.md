# Experimental Grammars Wishlist

This document tracks domain-specific languages and file formats that could benefit from tree-sitter parsing support. These are candidates for a future dedicated grammars project.

**Legend:**
- ✅ **Already in hypergumbo** - Implemented and tested
- 🔧 **Build from source** - Currently built via `scripts/build-source-grammars`
- 📦 **On PyPI** - Available as `pip install tree-sitter-<name>`
- 🆕 **Not yet available** - Would need grammar development

---

## Proof Assistants & Formal Methods

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Agda | `.agda` | ✅ Already in hypergumbo | Dependently typed proof assistant |
| Lean 4 | `.lean` | ✅ 🔧 Build from source | Theorem prover, [Julian/tree-sitter-lean](https://github.com/Julian/tree-sitter-lean) |
| Coq | `.v` | 🆕 | Proof assistant, grammar exists but not packaged |
| Isabelle | `.thy` | 🆕 | Proof assistant |
| Idris | `.idr` | 🆕 | Dependently typed language |
| F* | `.fst`, `.fsti` | 🆕 | Verification-oriented ML |
| Dafny | `.dfy` | 🆕 | Verification language (Microsoft) |
| TLA+ | `.tla` | 🆕 | Formal specification |

## Scientific Computing

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Wolfram/Mathematica | `.wl`, `.m`, `.nb` | ✅ 🔧 Build from source | [bostick/tree-sitter-wolfram](https://github.com/bostick/tree-sitter-wolfram) |
| Julia | `.jl` | ✅ Already in hypergumbo | Scientific computing |
| R | `.R`, `.r` | ✅ Already in hypergumbo | Statistical computing |
| Fortran | `.f90`, `.f95`, `.f03` | ✅ Already in hypergumbo | HPC, legacy scientific |
| MATLAB/Octave | `.m` | 🆕 | Numerical computing |
| SageMath | `.sage` | 🆕 | Computer algebra (Python-based) |

## Bioinformatics

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Nextflow | `.nf`, `nextflow.config` | 🆕 | Workflow DSL for genomics pipelines |
| Snakemake | `Snakefile`, `.smk` | 🆕 | Workflow DSL (Python-based) |
| CWL | `.cwl` | 🆕 | Common Workflow Language (YAML-based) |
| WDL | `.wdl` | 🆕 | Workflow Description Language (Broad Institute) |
| Galaxy | `.ga` | 🆕 | Galaxy workflow format |
| BioPython patterns | `.py` | ✅ Already in hypergumbo | Python with Bio.* imports |

## Computational Chemistry

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Gaussian | `.gjf`, `.com` | 🆕 | Quantum chemistry input |
| ORCA | `.inp` | 🆕 | Quantum chemistry |
| Q-Chem | `.in` | 🆕 | Quantum chemistry |
| NWChem | `.nw` | 🆕 | Computational chemistry |
| Psi4 | `.dat` | 🆕 | Quantum chemistry (Python-based) |
| GAMESS | `.inp` | 🆕 | Quantum chemistry |
| Molpro | `.com` | 🆕 | Quantum chemistry |

## Physics Simulation

### Molecular Dynamics / All-Atom Simulation

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| LAMMPS | `.lmp`, `.in` | 🆕 | Large-scale MD simulation |
| GROMACS | `.mdp`, `.top`, `.itp` | 🆕 | Biomolecular simulation |
| NAMD | `.namd`, `.conf` | 🆕 | Nanoscale MD simulation |
| AMBER | `.in`, `.prmtop` | 🆕 | Biomolecular simulation |
| OpenMM | `.py` | ✅ Already in hypergumbo | Python-based MD (detect patterns) |
| CP2K | `.inp` | 🆕 | Atomistic simulation |

### Solid State Physics / DFT

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| VASP | `INCAR`, `POSCAR`, `POTCAR` | 🆕 | DFT calculations |
| Quantum ESPRESSO | `.in`, `.pw` | 🆕 | DFT calculations |
| SIESTA | `.fdf` | 🆕 | DFT calculations |
| CASTEP | `.param`, `.cell` | 🆕 | DFT calculations |
| Abinit | `.in`, `.abi` | 🆕 | DFT calculations |
| GPAW | `.py` | ✅ Already in hypergumbo | Python-based DFT |

## Agent-Based Simulation

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| NetLogo | `.nlogo`, `.nls` | 🆕 | Multi-agent simulation |
| Mesa | `.py` | ✅ Already in hypergumbo | Python agent-based (detect patterns) |
| Repast | `.java` | ✅ Already in hypergumbo | Java agent-based (detect patterns) |
| GAMA | `.gaml` | 🆕 | Spatial agent-based modeling |
| Agents.jl | `.jl` | ✅ Already in hypergumbo | Julia agent-based |

## Game Development

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| GDScript | `.gd` | 📦 On PyPI | Godot scripting, `tree-sitter-gdscript` |
| Godot Shaders | `.gdshader` | 🆕 | Godot shader language |
| Godot Resources | `.tres`, `.tscn` | 🆕 | Godot scene/resource format |
| Lua (Roblox) | `.lua` | ✅ Already in hypergumbo | Roblox uses Luau variant |
| Luau | `.luau` | 🆕 | Roblox's typed Lua |
| UnrealScript | `.uc` | 🆕 | Unreal Engine (legacy) |
| Blueprints | `.uasset` | 🆕 | Unreal visual scripting (binary) |
| Unity ShaderLab | `.shader` | 🆕 | Unity shader language |
| Ren'Py | `.rpy` | 🆕 | Visual novel engine |

## Virtual Worlds / MMORPGs

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| LSL | `.lsl` | 🆕 | Linden Scripting Language (Second Life) |
| oSSL | `.ossl` | 🆕 | OpenSimulator scripting |
| MUD/MUSH | `.muf`, `.mpi` | 🆕 | Text-based virtual worlds |

## Creative Tools

### 3D Modeling / Animation

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Blender Python | `.py` | ✅ Already in hypergumbo | Blender addon patterns |
| USD | `.usda`, `.usdc` | 🆕 | Universal Scene Description (Pixar) |
| glTF | `.gltf` | ✅ Already in hypergumbo | JSON-based 3D format |
| FBX | `.fbx` | 🆕 | Autodesk interchange (binary) |
| Arnold OSL | `.osl` | 🆕 | Open Shading Language |
| MaterialX | `.mtlx` | 🆕 | Material exchange format |

### Video Production

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| EDL | `.edl` | 🆕 | Edit Decision List |
| OpenTimelineIO | `.otio` | 🆕 | Timeline interchange (JSON-based) |
| FFmpeg filters | — | 🆕 | Filter expressions |
| DaVinci Resolve scripts | `.py` | ✅ Already in hypergumbo | Python scripting API |
| After Effects expressions | `.jsx` | ✅ Already in hypergumbo | JavaScript expressions |

## CAD / Manufacturing

### 3D Printing

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| OpenSCAD | `.scad` | 📦 On PyPI? | Parametric CAD, check availability |
| G-code | `.gcode`, `.nc`, `.ngc` | 🆕 | CNC/3D printer instructions |
| STL (ASCII) | `.stl` | 🆕 | Simple geometry format |
| 3MF | `.3mf` | 🆕 | Modern 3D printing format (XML) |
| AMF | `.amf` | 🆕 | Additive manufacturing format |

### CAD Software

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| STEP | `.step`, `.stp` | 🆕 | ISO 10303 CAD exchange (very complex) |
| IGES | `.iges`, `.igs` | 🆕 | Legacy CAD format |
| DXF | `.dxf` | 🆕 | AutoCAD exchange |
| IFC | `.ifc` | 🆕 | Building Information Modeling |
| SolidWorks macros | `.swp` | 🆕 | VBA-based macros |
| AutoLISP | `.lsp` | 🆕 | AutoCAD scripting |
| Grasshopper | `.gh` | 🆕 | Rhino visual programming (binary) |
| Dynamo | `.dyn` | 🆕 | Revit visual programming |

## Biotechnology / Bioreactors

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| SBML | `.sbml`, `.xml` | 🆕 | Systems Biology Markup Language |
| SBOL | `.sbol`, `.xml` | 🆕 | Synthetic Biology Open Language |
| CellML | `.cellml` | 🆕 | Cell physiological models |
| SED-ML | `.sedml` | 🆕 | Simulation Experiment Description |
| NeuroML | `.nml` | 🆕 | Computational neuroscience |
| COMBINE Archive | `.omex` | 🆕 | Multi-format biology archives |
| BioNetGen | `.bngl` | 🆕 | Rule-based modeling |
| Antimony | `.ant` | 🆕 | Human-readable SBML |

## Nanotechnology

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| LAMMPS | `.lmp` | 🆕 | Nano-scale MD (see Physics) |
| NAMD | `.namd` | 🆕 | Nanoscale molecular dynamics |
| Quantum ESPRESSO | `.in` | 🆕 | Nano-scale DFT |
| DFTB+ | `.hsd` | 🆕 | Tight-binding DFT |
| ASE | `.py` | ✅ Already in hypergumbo | Atomic Simulation Environment (Python) |
| Atomsk | script format | 🆕 | Atomic structure manipulation |

## Legal / Legislative

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Catala | `.catala_en`, `.catala_fr` | 📦 On PyPI? | **Executable legislation!** Check availability |
| Akoma Ntoso | `.xml` | 🆕 | Legal document standard (XML) |
| LegalRuleML | `.xml` | 🆕 | Legal rules markup |
| LEOS | `.xml` | 🆕 | EU legislation editing |
| USLegal XML | `.xml` | 🆕 | US Code markup |
| Blackstone patterns | `.py` | ✅ Already in hypergumbo | spaCy legal NLP (Python) |

## Hardware Description

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Verilog | `.v`, `.sv` | ✅ Already in hypergumbo | Digital logic |
| VHDL | `.vhd`, `.vhdl` | ✅ Already in hypergumbo | Digital logic |
| SystemVerilog | `.sv` | ✅ Already in hypergumbo | Via Verilog analyzer |
| Chisel | `.scala` | ✅ Already in hypergumbo | Scala-based HDL |
| SpinalHDL | `.scala` | ✅ Already in hypergumbo | Scala-based HDL |
| Amaranth | `.py` | ✅ Already in hypergumbo | Python-based HDL |
| Bluespec | `.bsv` | 🆕 | High-level HDL |
| FIRRTL | `.fir` | 🆕 | Intermediate representation |

## Configuration & Infrastructure

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| HCL/Terraform | `.tf`, `.hcl` | ✅ Already in hypergumbo | Infrastructure as code |
| Nix | `.nix` | ✅ Already in hypergumbo | Package management |
| Dockerfile | `Dockerfile` | ✅ Already in hypergumbo | Container definitions |
| Kubernetes | `.yaml` | ✅ Already in hypergumbo | K8s manifests (YAML) |
| Ansible | `.yaml` | ✅ Already in hypergumbo | Automation playbooks |
| Puppet | `.pp` | 🆕 | Configuration management |
| Chef | `.rb` | ✅ Already in hypergumbo | Ruby-based config |
| Salt | `.sls` | 🆕 | YAML-based config |
| Pulumi | `.ts`, `.py` | ✅ Already in hypergumbo | IaC in general-purpose langs |

## Document Formats

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| LaTeX | `.tex` | ✅ Already in hypergumbo | Scientific documents |
| Typst | `.typ` | 📦 On PyPI | Modern LaTeX alternative |
| AsciiDoc | `.adoc` | 🆕 | Technical documentation |
| reStructuredText | `.rst` | 🆕 | Python documentation |
| Org Mode | `.org` | 🆕 | Emacs outliner/notes |
| Markdown | `.md` | 🆕 | Would detect frontmatter, links |
| Jupyter | `.ipynb` | 🆕 | Notebook cell structure (JSON) |
| Quarto | `.qmd` | 🆕 | Scientific publishing |
| R Markdown | `.Rmd` | 🆕 | R + Markdown |

## Emerging / Interesting

| Grammar | Files | Status | Notes |
|---------|-------|--------|-------|
| Mojo | `.mojo`, `.🔥` | 🆕 | Python superset for AI |
| Zig | `.zig` | ✅ Already in hypergumbo | Systems programming |
| Vale | `.vale` | 🆕 | Memory-safe systems lang |
| Roc | `.roc` | 🆕 | Fast functional language |
| Unison | `.u` | 🆕 | Content-addressed code |
| Koka | `.kk` | 🆕 | Effect-typed language |
| Gleam | `.gleam` | 📦 On PyPI | Type-safe BEAM language |
| Pkl | `.pkl` | 🆕 | Apple's configuration language |

---

## Priority Tiers

### Tier 1: High Value, Likely Available
Grammars that would unlock significant domain value and likely have existing tree-sitter implementations:

1. **GDScript** - Godot is huge in indie gamedev
2. **Typst** - Growing LaTeX alternative
3. **Catala** - Computational law is fascinating
4. **OpenSCAD** - Popular in 3D printing community
5. **Nextflow/Snakemake** - Bioinformatics workflows

### Tier 2: High Value, Requires Work
Important domains but grammars may need development:

1. **LAMMPS/GROMACS** - Molecular dynamics dominates computational science
2. **NetLogo** - Agent-based simulation standard
3. **VASP/Quantum ESPRESSO** - Solid state physics workhorse
4. **SBML/SBOL** - Synthetic biology standards

### Tier 3: Niche but Interesting
Smaller communities but unique value:

1. **LSL** - Second Life/OpenSim scripting
2. **Akoma Ntoso** - Legal document standard
3. **USD** - Pixar's scene format (growing in VFX/games)
4. **G-code** - Direct machine control

---

## Notes

- Many scientific input formats are line-oriented with keywords, making them good candidates for simple tree-sitter grammars
- XML-based formats (SBML, CellML, Akoma Ntoso) could potentially use the existing XML analyzer with domain-specific pattern detection
- Python-based tools (Mesa, ASE, OpenMM) already work via Python analyzer; could add framework-specific pattern detection
- Binary formats (FBX, Blueprints) are not candidates for tree-sitter

*Last updated: 2025-12-28*
