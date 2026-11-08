# Starter pack: source and build

`pack/` holds the hand-written files of the participant starter pack (README, skill, format document, `starter.py`, templates, examples, Lean-check guide).
`build_starter_pack.py` assembles the pack from them, the whole `evaluation/engine` (tests and caches excluded), ForShor's two printed reference documents from the engine's data, and a
snapshot of the results boards taken from the seed (`--seed`, the exporter's epoch input), pins every example's output with the pack's own engine, writes `MANIFEST.json` and the zip.

```sh
python evaluation/starter/build_starter_pack.py --seed <epoch-input.json> --out <dir> --python <python with the pack's requirements>
python evaluation/starter/test_pack_docs.py <dir>/forshor-challenge-starter                  # no stale references in what a participant reads
python evaluation/starter/test_pack_matches_seed.py <dir>/forshor-challenge-starter.zip <epoch-input.json> --python <python>
                                                                                              # the unzipped pack prices every seed policy to the boards' T and Q
```

The pack changes whenever `evaluation/engine` does (its pinned outputs and `MANIFEST.json` go with it), so rebuild it after any engine change and after each re-seed.
Nothing under `evaluation/engine/` is touched by the pack. The site serves the result from `public/starter/` (forshor-site).
