## What this changes

<!-- One or two sentences. If it adds a finding code, say which and why it is
     actionable: what is wrong, which references, and what to do about it. -->

## Checks

- [ ] `scripts/lint.sh && scripts/test.sh` pass.
- [ ] New or changed behaviour has a test.
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`, if this is user-visible.

CONTRIBUTING.md asks two questions that CI cannot answer for you. Answer the
ones that apply, and delete the rest.

- [ ] **Touched anything under `src/pinside/kicad/`?** The KiCad tests run in
      CI in a container, but a real KiCad install is the only place `project`
      output gets opened. Say which KiCad version you ran against.
- [ ] **Touched anything under `src/pinside/firmware/templates/`?** CI compiles
      the host tests with the mock HAL and cross-compiles against the Pico SDK.
      Say whether you flashed it and what the fixture did.
- [ ] **Added or changed a target in `targets.py`?** Name the datasheet and the
      table you checked the function map against. A wrong map produces a config
      that validates and does not work.
- [ ] **Changed a probe in `pogo.py`?** Cite the supplier drawing. These numbers
      end up as drill sizes on a board somebody orders.
