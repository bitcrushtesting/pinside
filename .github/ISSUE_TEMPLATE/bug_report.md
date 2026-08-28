---
name: Bug report
about: Something pinside got wrong
labels: bug
---

## What happened

<!-- The command you ran and what it printed. Findings are quotable verbatim;
     their codes are stable. -->

```
$ pinside ...
```

## What you expected

## The board or config

pinside reads local files, so the fastest fix usually starts from the input.
If the `.kicad_pcb` or the fixture config cannot be shared, the relevant part
usually can: a footprint, an `Edge.Cuts` shape, one channel out of the config.

## Versions

- pinside: <!-- pinside --version -->
- Python: <!-- python3 --version -->
- KiCad, if `project` is involved:
- OS:
